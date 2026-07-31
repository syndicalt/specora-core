"""Generate the container artefacts: Dockerfiles, compose, entrypoints, requirements.

Dependency policy for the generated app image:

  - **Only what `backend/` imports at runtime.** The runtime image previously
    installed pytest, and the healer image's CLI/LLM stack was audited as part
    of the same set. A deployed application should not carry a test framework.
    Test-only dependencies go to `requirements-dev.txt`, which the Dockerfile
    does not copy.
  - **Every requirement is bounded on both sides.** An unbounded `fastapi>=…`
    is how a future major release turns a deprecation (`@app.on_event`) into a
    boot failure in an image nobody rebuilt deliberately. Lower bounds are set
    at the first release without a known advisory for that package, so
    `pip-audit` resolves clean.

Container policy:

  - **Nothing in the stack runs as root.** Both images create a system account
    and `USER` into it; the database service runs as the `postgres` uid rather
    than letting the official entrypoint start as root and step down.
  - **Application code is owned by root and writable by nobody.** The process
    that serves requests cannot rewrite the code it is serving, so an RCE has
    no in-image persistence path.
  - **Secrets arrive as files, not environment variables.** See
    `_SECRET_FILE_HELPER` for the full argument.
"""

from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile

# Both images are built from this. Kept as a build ARG rather than a literal so
# an operator who wants a reproducible build can pin a digest at build time
# (`--build-arg PYTHON_IMAGE=python@sha256:…`) without editing generated files.
#
# The default is deliberately a tag and not a digest. This generator has no
# update mechanism for a digest it bakes in, so a pinned one would freeze every
# generated application on whatever base image existed when this file was last
# edited — which is strictly *less* fresh than a tag that Docker Official
# Images rebuilds with OS security patches. The minor version is pinned, which
# is the part that changes behaviour; the patch level is not, which is the part
# that carries CVE fixes.
_PYTHON_IMAGE = "python:3.12-slim"

# Fixed uid/gid rather than "whatever useradd picks next". Bind mounts and
# volumes carry numeric ownership across the container boundary, so an
# unpredictable uid makes a mounted path's permissions unpredictable too.
_APP_UID = 10001
_HEALER_UID = 10002

# The uid the postgres official image assigns to its `postgres` account.
_POSTGRES_UID = 999

# Emitted as a single Dockerfile line. A `\` continuation would land inside the
# quoted Python snippet, where the Dockerfile parser's line-joining and the
# shell's quoting rules interact in a way nobody should have to reason about.
_APP_HEALTHCHECK = (
    "CMD python -c \"import os,urllib.request as u; u.urlopen("
    "'http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health', timeout=4).read()\""
)
_HEALER_HEALTHCHECK = (
    "CMD python -c \"import os,urllib.request as u; u.urlopen("
    "'http://127.0.0.1:'+os.environ.get('SPECORA_HEALER_PORT','8083')"
    "+'/healer/health', timeout=4).read()\""
)

_SECRET_FILE_HELPER = '''
# Resolve the `*_FILE` secret convention.
#
# Anything under compose's `environment:` is readable with `docker inspect`, is
# echoed by `docker compose config`, is inherited by every child process, and is
# captured verbatim by crash reporters that dump the environment. A file is
# readable only by a process that opens it.
#
# This does not make the secret invisible: backend/config.py reads os.environ,
# so the value ends up in this process's environment either way and remains
# readable at /proc/<pid>/environ *inside* the container. What it removes is the
# secret's presence in the Docker daemon's stored container configuration and in
# every tool that reads it. That is the whole of the improvement, and it is
# worth stating plainly rather than implying more.
#
# Plain environment variables stay supported, because Kubernetes, ECS and Fly
# all inject secrets that way. Setting both forms of one secret is an error
# rather than a silent precedence rule: whichever way it resolved, half the
# operators would have guessed wrong.
resolve_file_secrets() {
    local name file_var path value
    for name in "$@"; do
        file_var="${name}_FILE"
        path="${!file_var:-}"
        [ -n "$path" ] || continue
        if [ -n "${!name:-}" ]; then
            echo "[specora] Both $name and $file_var are set; pick one." >&2
            exit 1
        fi
        if [ ! -r "$path" ]; then
            echo "[specora] $file_var=$path is not a readable file." >&2
            exit 1
        fi
        # $(<file) strips trailing newlines. `echo secret > file` appends one,
        # and an HMAC key with a stray newline verifies against nothing while
        # looking identical to the one the operator thinks they set.
        value="$(<"$path")"
        if [ -z "$value" ]; then
            echo "[specora] $file_var=$path is empty." >&2
            exit 1
        fi
        export "$name=$value"
    done
}
'''

# Bounded, and fails fast on errors that waiting cannot fix. The previous
# implementation was `until python -c "connect"; do sleep 1; done` with stderr
# sent to /dev/null: a wrong password, a missing database or a typo in the DSN
# all presented as a container that hung on boot forever, with no output, and
# the orchestrator saw a slow start rather than a failed deploy.
_DB_WAIT = '''
if [ "${DATABASE_BACKEND:-postgres}" = "postgres" ]; then
    echo "[specora] Waiting for database (up to ${DATABASE_WAIT_TIMEOUT_SECONDS:-60}s)..."
    python - <<'PY'
import asyncio
import os
import sys
import time

import asyncpg

# These are answers from the server, not failures to reach it. Retrying them
# changes nothing; surfacing them is the entire point.
PERMANENT = (
    asyncpg.exceptions.InvalidPasswordError,
    asyncpg.exceptions.InvalidCatalogNameError,
    asyncpg.exceptions.InvalidAuthorizationSpecificationError,
)
# Reachable but not serving yet: still starting, or shutting down.
TRANSIENT = (
    OSError,
    TimeoutError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
)

url = os.environ.get("DATABASE_URL", "")
if not url:
    sys.exit("[specora] DATABASE_BACKEND=postgres but DATABASE_URL is unset.")

budget = float(os.environ.get("DATABASE_WAIT_TIMEOUT_SECONDS", "60"))
deadline = time.monotonic() + budget


async def probe() -> None:
    conn = await asyncpg.connect(url, timeout=5)
    await conn.close()


last = "no attempt completed"
while True:
    try:
        asyncio.run(probe())
        break
    except PERMANENT as exc:
        # The DSN is not printed: it carries the password.
        sys.exit(
            f"[specora] The database rejected this connection and retrying "
            f"will not change that: {type(exc).__name__}: {exc}"
        )
    except TRANSIENT as exc:
        last = f"{type(exc).__name__}: {exc}"
    # Anything else propagates. An unrecognised failure during boot should
    # abort the deploy with a traceback, not be retried until a timeout.
    if time.monotonic() >= deadline:
        sys.exit(
            f"[specora] Database not reachable within {budget:.0f}s. "
            f"Last error: {last}"
        )
    time.sleep(1)

print("[specora] Database is ready.")
PY
fi
'''


def generate_docker(ir: DomainIR) -> list[GeneratedFile]:
    has_auth = any(i.category == "auth" for i in ir.infra)
    return [
        _generate_dockerfile(ir),
        _generate_dockerignore(ir),
        _generate_entrypoint(ir, has_auth),
        _generate_healer_dockerfile(ir),
        _generate_healer_entrypoint(ir),
        _generate_compose(ir, has_auth),
        _generate_state_dir_readme(ir),
        _generate_init_secrets(ir, has_auth),
        _generate_env_example(ir, has_auth),
        _generate_requirements(ir, has_auth),
        _generate_dev_requirements(ir),
        _generate_healer_requirements(ir),
    ]


# =============================================================================
# Application image
# =============================================================================


def _generate_dockerfile(ir: DomainIR) -> GeneratedFile:
    # requirements-dev.txt is deliberately not copied: the runtime image must
    # not contain the test framework.
    content = f"""# @generated from domain/{ir.domain}
ARG PYTHON_IMAGE={_PYTHON_IMAGE}

# ── Build stage ──────────────────────────────────────────────────────────────
# The C toolchain lives here and nowhere else. asyncpg and argon2-cffi publish
# wheels for the common platforms but not for all of them, and the alternative
# to a build stage is either a runtime image that ships gcc or a build that
# fails on any architecture without a prebuilt wheel.
#
# This does not remove pip from the runtime image — the Python base image
# provides its own, and deleting it would mean hardcoding a site-packages path
# that PYTHON_IMAGE can be overridden out from under. What actually denies an
# attacker somewhere to install to is the read-only root filesystem set in
# docker-compose.yml.
FROM ${{PYTHON_IMAGE}} AS builder

# Ahead of the COPY so a requirements change does not re-run apt.
RUN apt-get update \\
 && apt-get install --no-install-recommends -y build-essential \\
 && rm -rf /var/lib/apt/lists/*

# Dependencies change far less often than application code, so they are their
# own layer: editing a route rebuilds one layer instead of reinstalling the
# whole dependency set.
COPY requirements.txt .
RUN python -m venv /opt/venv \\
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \\
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM ${{PYTHON_IMAGE}} AS runtime

ENV VIRTUAL_ENV=/opt/venv \\
    PATH=/opt/venv/bin:$PATH \\
    PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1

# A fixed uid, because volume and bind-mount ownership crosses the container
# boundary as a number. `--no-create-home` and a nologin shell because this
# account exists to own a process, not to be logged into.
RUN groupadd --system --gid {_APP_UID} app \\
 && useradd --system --uid {_APP_UID} --gid app --no-create-home \\
            --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

# Copied as root and never chowned to `app`: the process serving requests
# cannot rewrite the code it is serving, so remote code execution has no
# in-image persistence path. The compiled bytecode is produced here, while the
# filesystem is still writable, because PYTHONDONTWRITEBYTECODE is set above
# and the root filesystem is mounted read-only in production.
COPY backend/ backend/
COPY database/ database/
RUN python -m compileall -q backend

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/entrypoint.sh

USER {_APP_UID}:{_APP_UID}
EXPOSE 8000

# Liveness, not readiness: /health proves the event loop is still accepting and
# answering requests. It deliberately does not touch the database — a database
# outage should not make every replica restart, which is what a failing
# healthcheck causes. The dependency on the database is enforced at boot
# instead, by the wait in entrypoint.sh and the migration in the lifespan.
#
# start-period covers the database wait plus the migration run, during which
# failures do not count against the retry budget.
HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=3 \\
  {_APP_HEALTHCHECK}

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
"""
    return GeneratedFile(path="Dockerfile", content=content, provenance=f"domain/{ir.domain}")


def _generate_dockerignore(ir: DomainIR) -> GeneratedFile:
    # The build context is uploaded to the daemon in full, whatever the
    # Dockerfile then COPYs. Without this, `secrets/` and `.env` are handed to
    # the daemon on every build and land in the build cache.
    content = """secrets/
.env
.env.*
!.env.example
.forge/
.git/
**/__pycache__/
**/*.pyc
frontend/node_modules/
frontend/.next/
"""
    return GeneratedFile(path=".dockerignore", content=content, provenance=f"domain/{ir.domain}")


def _generate_entrypoint(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    secrets = ["DATABASE_URL", "SPECORA_HEALER_INGEST_TOKEN"]
    if has_auth:
        secrets.insert(0, "AUTH_SECRET")

    # Schema and migrations are applied by the application's lifespan handler,
    # not here. There used to be two implementations: this one recorded applied
    # migrations in `_specora_migrations` while backend/app.py kept its own
    # `_migrations` ledger, so every migration was applied twice on a fresh
    # database. The app-side runner is the one that can take an advisory lock
    # and wrap each migration in a transaction, so it is the one that survives.
    content = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        + _SECRET_FILE_HELPER
        + "\nresolve_file_secrets "
        + " ".join(secrets)
        + "\n"
        + _DB_WAIT
        + '''
echo "[specora] Starting app (schema and migrations run at startup)..."

# Worker count is read by uvicorn from $WEB_CONCURRENCY and left unset here,
# because the right number depends on the host and on DATABASE_POOL_MAX_SIZE —
# each worker opens its own pool, so total connections are workers x pool size
# and it is the database's max_connections that runs out first.
#
# --timeout-graceful-shutdown bounds how long in-flight requests have after
# SIGTERM. It must stay below the orchestrator's kill timeout (compose:
# stop_grace_period) or the difference is spent being SIGKILLed mid-request.
#
# --no-server-header drops the `server: uvicorn` response header. Version
# fingerprinting is not an attack on its own; it is what tells an attacker
# which one to try.
#
# Proxy headers are NOT trusted by default. uvicorn reads $FORWARDED_ALLOW_IPS
# itself, so a deployment behind a load balancer sets that variable to the
# balancer's address. Defaulting it open would let any client claim any source
# address in the access log, and this application reads nothing else from the
# forwarded headers today.
exec uvicorn backend.app:app \\
    --host 0.0.0.0 \\
    --port "${PORT:-8000}" \\
    --timeout-graceful-shutdown "${UVICORN_GRACEFUL_TIMEOUT:-20}" \\
    --no-server-header
'''
    )
    return GeneratedFile(
        path="entrypoint.sh",
        content=content,
        provenance=f"domain/{ir.domain}",
        # The Dockerfile chmods its own copy, so the image works either way. On
        # disk it must be executable too: a script that runs in the container
        # and not in the bundle is a difference an operator finds the hard way.
        executable=True,
    )


# =============================================================================
# Healer image
# =============================================================================


def _generate_healer_dockerfile(ir: DomainIR) -> GeneratedFile:
    content = f"""# @generated from domain/{ir.domain}
ARG PYTHON_IMAGE={_PYTHON_IMAGE}

FROM ${{PYTHON_IMAGE}} AS builder
RUN apt-get update \\
 && apt-get install --no-install-recommends -y build-essential \\
 && rm -rf /var/lib/apt/lists/*
COPY requirements.healer.txt requirements.txt
RUN python -m venv /opt/venv \\
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip setuptools wheel \\
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM ${{PYTHON_IMAGE}} AS runtime

# specora-core is mounted read-only at /specora-core by docker-compose.
ENV VIRTUAL_ENV=/opt/venv \\
    PATH=/opt/venv/bin:$PATH \\
    PYTHONPATH=/specora-core \\
    PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    HOME=/tmp

RUN groupadd --system --gid {_HEALER_UID} healer \\
 && useradd --system --uid {_HEALER_UID} --gid healer --no-create-home \\
            --home-dir /app --shell /usr/sbin/nologin healer

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY entrypoint.healer.sh /usr/local/bin/entrypoint.healer.sh
RUN chmod 0555 /usr/local/bin/entrypoint.healer.sh

# This container writes: the ticket database under /app/.forge, and the
# contracts and regenerated code under /app/domains, /app/backend, /app/database
# and /app/frontend. All of those are bind mounts from the host, so the uid
# below has to be able to write to the host directories — see the `user:` key on
# the healer service in docker-compose.yml.
USER {_HEALER_UID}:{_HEALER_UID}
EXPOSE 8083

# /healer/health is the one endpoint exempt from the ingest token, precisely so
# an orchestrator can probe it without holding a credential. It discloses
# liveness and nothing else.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \\
  {_HEALER_HEALTHCHECK}

ENTRYPOINT ["/usr/local/bin/entrypoint.healer.sh"]
"""
    return GeneratedFile(
        path="Dockerfile.healer", content=content, provenance=f"domain/{ir.domain}"
    )


def _generate_healer_entrypoint(ir: DomainIR) -> GeneratedFile:
    # The approval secret signs the links that apply contract fixes, and the
    # provider keys buy LLM tokens. Both belong in files for the same reason the
    # app's signing key does.
    secrets = [
        "SPECORA_HEALER_INGEST_TOKEN",
        "SPECORA_HEALER_APPROVAL_SECRET",
        "SPECORA_HEALER_OPERATOR_TOKEN",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "XAI_API_KEY",
        "ZAI_API_KEY",
    ]
    content = (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        + _SECRET_FILE_HELPER
        + "\nresolve_file_secrets "
        + " ".join(secrets)
        + '''

# --host 0.0.0.0 is safe only because port 8083 is not published to the host.
# The `serve` command defaults to loopback for exactly that reason; inside a
# container loopback would make the app container unable to reach it.
exec python -m forge.cli.main healer serve \\
    --port "${SPECORA_HEALER_PORT:-8083}" \\
    --host 0.0.0.0
'''
    )
    return GeneratedFile(
        path="entrypoint.healer.sh",
        content=content,
        provenance=f"domain/{ir.domain}",
        executable=True,
    )


# =============================================================================
# Compose
# =============================================================================


def _generate_compose(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    auth_secret_mount = ""
    auth_secret_env = ""
    auth_secret_decl = ""
    if has_auth:
        auth_secret_env = "\n      AUTH_SECRET_FILE: /run/secrets/auth_secret"
        auth_secret_mount = "\n      - auth_secret"
        auth_secret_decl = "\n  auth_secret:\n    file: ./secrets/auth_secret"

    content = f"""# @generated from domain/{ir.domain}
#
# Before the first `docker compose up`, create the secret files:
#
#     ./init-secrets.sh
#
# Nothing in this file carries a default credential, so the stack refuses to
# start until that has been done. That is deliberate: a stack that boots with a
# built-in password is a stack that reaches production with one.
#
# Credentials reach each service as files under /run/secrets, which its
# entrypoint reads and exports. One consequence worth knowing before it
# surprises you: `docker compose exec app <cmd>` starts a process that does not
# go through the entrypoint, so it will not see AUTH_SECRET or DATABASE_URL.
# That is the point — those values are not in the container's declared
# environment — but a one-off command that needs them has to read
# /run/secrets/<name> itself.
#
# Every service here is non-root, drops all capabilities, cannot gain new
# privileges, has a read-only root filesystem with its writable paths named
# explicitly, and is bounded in CPU, memory and log volume. The reasoning for
# each is inline below, at the first service that uses it.

# The compose project name. Pinned so two generated stacks on one host do not
# collide on a project name derived from whatever the output directory is called.
name: {ir.domain}

# Container logs are unbounded by default: json-file grows until the disk is
# full, and the first symptom is every container on the host failing at once.
x-logging: &logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

x-hardening: &hardening
  # A container that cannot acquire privileges through setuid binaries. Without
  # it, `cap_drop` is undone by any setuid-root binary left in the base image.
  security_opt:
    - no-new-privileges:true
  # None of these processes bind a privileged port, change uid, mount, or load
  # a kernel module. Everything they legitimately do is unprivileged, so the
  # whole capability set goes.
  cap_drop:
    - ALL
  logging: *logging

services:
  db:
    # Major version pinned: PGDATA written by 17 cannot be read by 16, so an
    # unpinned major turns `docker compose pull` into an unrecoverable stack.
    # The minor is deliberately not pinned — minor releases are in-place
    # compatible and are how this image ships security fixes.
    image: postgres:16
    <<: *hardening
    restart: unless-stopped
    # The official entrypoint normally starts as root to fix ownership and then
    # steps down with gosu, which needs CAP_SETUID/CAP_SETGID and so is
    # incompatible with cap_drop: ALL. Starting as the postgres uid directly
    # skips the step-down entirely. The named volume below inherits its
    # ownership from the image, which already owns that path as this uid.
    user: "{_POSTGRES_UID}:{_POSTGRES_UID}"
    environment:
      POSTGRES_DB: specora
      POSTGRES_USER: specora
      # Natively supported by this image. See ./init-secrets.sh.
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    # Not published. The application reaches the database over the compose
    # network; publishing it put a database on a host interface. For a psql
    # session use `docker compose exec db psql -U specora`.
    volumes:
      - pgdata:/var/lib/postgresql/data
    read_only: true
    # Everything postgres writes outside PGDATA. The unix socket directory and
    # the temporary-file area are the only two, and both are per-container
    # state that must not survive a restart anyway.
    tmpfs:
      - /tmp:size=64m,mode=1777
      - /var/run/postgresql:size=16m,mode=0755,uid={_POSTGRES_UID},gid={_POSTGRES_UID}
    healthcheck:
      # -U/-d because the default pg_isready target is the current OS user,
      # which reports "accepting connections" before this database exists.
      test: ["CMD-SHELL", "pg_isready -U specora -d specora"]
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 30s
    deploy:
      resources:
        limits:
          # An unbounded container is a host outage: one runaway query with a
          # large work_mem takes down every other service on the machine. Size
          # these to the deployment; the defaults are a starting point, not a
          # recommendation.
          cpus: "${{DB_CPU_LIMIT:-2}}"
          memory: ${{DB_MEMORY_LIMIT:-1g}}

  app:
    build:
      context: .
      # Pin a base image digest for a reproducible build:
      #   PYTHON_IMAGE=python@sha256:… docker compose build
      args:
        PYTHON_IMAGE: ${{PYTHON_IMAGE:-{_PYTHON_IMAGE}}}
    <<: *hardening
    restart: unless-stopped
    ports:
      # Loopback by default. This process terminates plain HTTP and sets a
      # Secure refresh cookie, so it is meant to sit behind a TLS-terminating
      # proxy rather than face the network itself. Put that proxy on this
      # compose network, or set APP_BIND_ADDRESS=0.0.0.0 knowingly.
      - "${{APP_BIND_ADDRESS:-127.0.0.1}}:${{APP_PORT:-8000}}:8000"
    # Non-secret configuration only. Optional, so `docker compose config` works
    # on a fresh checkout; the application still refuses to boot without the
    # values it requires.
    env_file:
      - path: .env
        required: false
    environment:
      DATABASE_BACKEND: postgres
      # The DSN carries the database password, so it is a secret in full.
      DATABASE_URL_FILE: /run/secrets/database_url
      SPECORA_HEALER_URL: http://healer:8083
      SPECORA_HEALER_INGEST_TOKEN_FILE: /run/secrets/healer_ingest_token{auth_secret_env}
    secrets:
      - database_url
      - healer_ingest_token{auth_secret_mount}
    depends_on:
      db:
        condition: service_healthy
      # No dependency on `healer`. Error reporting is best-effort, and making
      # the application wait on its observability sidecar turns a degraded
      # feedback loop into a failed deploy.
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
    # Must exceed --timeout-graceful-shutdown in entrypoint.sh, or the
    # difference is spent being SIGKILLed with requests still in flight.
    stop_grace_period: 30s
    deploy:
      resources:
        limits:
          cpus: "${{APP_CPU_LIMIT:-2}}"
          memory: ${{APP_MEMORY_LIMIT:-1g}}

  healer:
    build:
      context: .
      dockerfile: Dockerfile.healer
      args:
        PYTHON_IMAGE: ${{PYTHON_IMAGE:-{_PYTHON_IMAGE}}}
    <<: *hardening
    restart: unless-stopped
    # Deliberately unpublished. Two different surfaces share this port:
    #   - the data plane (/healer/ingest), which only `app` calls, over the
    #     compose network at http://healer:8083;
    #   - the control plane (approvals), which applies contract fixes and so is
    #     the most privileged surface in the stack.
    # Publishing 8083 puts both on the host interface. Expose the control plane
    # deliberately, behind a reverse proxy that terminates TLS and authenticates
    # — not by publishing the whole port here.
    #
    # This service writes to bind mounts, so its uid has to match the owner of
    # this directory on the host. Override when that is not uid 1000:
    #   SPECORA_HEALER_UID=$(id -u) SPECORA_HEALER_GID=$(id -g) docker compose up
    user: "${{SPECORA_HEALER_UID:-1000}}:${{SPECORA_HEALER_GID:-1000}}"
    env_file:
      - path: .env
        required: false
    environment:
      SPECORA_HEALER_PORT: "8083"
      SPECORA_HEALER_INGEST_TOKEN_FILE: /run/secrets/healer_ingest_token
      # Signs the approve/reject links. Whoever holds it can apply any proposed
      # contract fix, which is a code deployment.
      SPECORA_HEALER_APPROVAL_SECRET_FILE: /run/secrets/healer_approval_secret
    secrets:
      - healer_ingest_token
      - healer_approval_secret
    volumes:
      - ./domains:/app/domains
      - ./.forge:/app/.forge
      - ./backend:/app/backend
      - ./database:/app/database
      - ./frontend:/app/frontend
      - ${{SPECORA_CORE_PATH:-./../specora-core}}:/specora-core:ro
    read_only: true
    tmpfs:
      - /tmp:size=256m,mode=1777
    deploy:
      resources:
        limits:
          cpus: "${{HEALER_CPU_LIMIT:-1}}"
          # Higher than it looks like it needs: a proposal run holds a whole
          # contract set plus an LLM response in memory, and an OOM kill here
          # loses the ticket that was being worked on.
          memory: ${{HEALER_MEMORY_LIMIT:-1g}}

  # Frontend — behind a profile because npm install is slow in Docker on Windows.
  # Run locally with: cd frontend && npm install && npm run dev
  # Or include in Docker with: docker compose --profile frontend up
  frontend:
    profiles: [frontend]
    build:
      context: ./frontend
      dockerfile: Dockerfile.frontend
      args:
        # Next.js inlines NEXT_PUBLIC_* into the client bundle at build time,
        # so this has to be a build argument — as a runtime environment
        # variable it would have no effect on anything the browser runs. The
        # value must be what the *browser* can reach, which is why it is not
        # the compose-internal `http://app:8000`.
        NEXT_PUBLIC_API_URL: ${{NEXT_PUBLIC_API_URL:-http://localhost:8000}}
    <<: *hardening
    restart: unless-stopped
    ports:
      - "${{FRONTEND_BIND_ADDRESS:-127.0.0.1}}:${{FRONTEND_PORT:-3000}}:3000"
    environment:
      # Next.js standalone reads its bind address from $HOSTNAME, and inside a
      # container that resolves to the eth0 address alone — so the server does
      # not listen on loopback and the healthcheck below cannot reach it. This
      # is what the upstream Next.js Docker example sets, for the same reason.
      HOSTNAME: "0.0.0.0"
      NEXT_TELEMETRY_DISABLED: "1"
    read_only: true
    tmpfs:
      - /tmp:size=64m,mode=1777
      # Next.js writes its incremental cache here even when nothing is
      # statically regenerated.
      - /app/.next/cache:size=128m,mode=0777
    healthcheck:
      test:
        - CMD
        - node
        - -e
        - >-
          fetch('http://127.0.0.1:3000/')
          .then(r => process.exit(r.ok ? 0 : 1))
          .catch(() => process.exit(1))
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    deploy:
      resources:
        limits:
          cpus: "${{FRONTEND_CPU_LIMIT:-1}}"
          memory: ${{FRONTEND_MEMORY_LIMIT:-512m}}

volumes:
  pgdata:

# File-backed secrets. Compose bind-mounts each file at /run/secrets/<name>
# read-only, preserving its mode on the host — which is why ./init-secrets.sh
# writes them 0644 inside a 0700 directory: the directory keeps other host users
# out, and the file mode is what the unprivileged container uid needs to read.
secrets:
  db_password:
    file: ./secrets/db_password
  database_url:
    file: ./secrets/database_url
  healer_ingest_token:
    file: ./secrets/healer_ingest_token
  healer_approval_secret:
    file: ./secrets/healer_approval_secret{auth_secret_decl}
"""
    return GeneratedFile(
        path="docker-compose.yml", content=content, provenance=f"domain/{ir.domain}"
    )


def _generate_state_dir_readme(ir: DomainIR) -> GeneratedFile:
    """Ship a file under `.forge/` so the directory exists before compose mounts it.

    Docker creates a missing bind-mount source itself, as **root**. The healer
    runs unprivileged, so on a stock deploy it started, answered its health
    probe, and then crashed on every queue drain with
    `PermissionError: '.forge/healer'` — a sidecar reporting healthy while the
    self-healing loop was dead. Writing anything into the directory from the
    generator means it exists, owned by whoever generated the bundle, which is
    the uid the compose `user:` key defaults to.
    """
    content = f"""# `.forge/` — Healer state for {ir.domain}

Runtime state, not generated code. Nothing here is derived from the contracts,
so regenerating the bundle will not recreate it.

`healer/healer.db`
: The ticket queue: every error report, proposal and decision.

`healer/inbox/`
: Error payloads dropped as files, for callers that cannot reach the HTTP
  ingest endpoint.

`diffs/`
: The audit trail. One JSON record per applied contract change, including
  which actor approved it.

This directory is bind-mounted into the healer container, which runs as an
unprivileged uid. It is committed to the bundle with this file because Docker
creates a missing bind-mount source as root, and the healer would then be
unable to write its own queue.

Back up `diffs/` with the contracts. It is the only record of who changed the
source of truth and why; the contracts themselves show only the result.
"""
    return GeneratedFile(
        path=".forge/README.md", content=content, provenance=f"domain/{ir.domain}"
    )


def _generate_init_secrets(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    auth_block = ""
    if has_auth:
        auth_block = """
# Signs every access and refresh token this application issues. config.py
# refuses to boot on anything shorter than 32 characters.
new_secret auth_secret
"""

    content = f'''#!/bin/bash
# @generated from domain/{ir.domain}
#
# Create the secret files docker-compose.yml expects. Idempotent: an existing
# file is never overwritten, so re-running this after adding a service does not
# rotate the credentials already in use.
set -euo pipefail
cd "$(dirname "$0")"

DIR=secrets
mkdir -p "$DIR"
# 0700 on the directory is what keeps other users on this host out. The files
# themselves are 0644 because compose bind-mounts them into the container with
# their host mode intact, and the container processes run as an unprivileged
# uid that is not the owner — a 0600 file would simply be unreadable there.
chmod 0700 "$DIR"

# 32 bytes from the kernel CSPRNG, hex-encoded. `od` rather than `openssl`
# because it is in coreutils and needs no package to be installed.
random_hex() {{
    od -vAn -N32 -tx1 /dev/urandom | tr -d ' \\n'
}}

new_secret() {{
    local name="$1"
    if [ -e "$DIR/$name" ]; then
        echo "  keep   $DIR/$name (already exists)"
        return
    fi
    random_hex > "$DIR/$name"
    chmod 0644 "$DIR/$name"
    echo "  create $DIR/$name"
}}

new_secret db_password
{auth_block}
# Shared between the app and the healer: the app presents it on /healer/ingest.
new_secret healer_ingest_token

# Signs approve/reject links. Holding it is equivalent to being able to deploy.
new_secret healer_approval_secret

# Derived, not generated: it has to agree with db_password, and the database
# service is reachable as `db` on the compose network.
if [ ! -e "$DIR/database_url" ]; then
    printf 'postgresql://specora:%s@db:5432/specora' "$(cat "$DIR/db_password")" \\
        > "$DIR/database_url"
    chmod 0644 "$DIR/database_url"
    echo "  create $DIR/database_url"
else
    echo "  keep   $DIR/database_url (already exists)"
    if ! grep -qF "$(cat "$DIR/db_password")" "$DIR/database_url"; then
        echo "  WARN   database_url does not contain the current db_password;" >&2
        echo "         the application will fail to authenticate." >&2
    fi
fi

echo
echo "Secrets are in ./$DIR. They are not in .env and not in docker-compose.yml,"
echo "so they do not appear in \\`docker inspect\\` or \\`docker compose config\\`."
echo "Back them up: losing auth_secret invalidates every issued token, and"
echo "losing db_password locks you out of the pgdata volume."
'''
    return GeneratedFile(
        path="init-secrets.sh",
        content=content,
        provenance=f"domain/{ir.domain}",
        # Nothing else in the bundle runs before this does.
        executable=True,
    )


# =============================================================================
# .env.example
# =============================================================================


def _generate_env_example(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    lines = [
        "# =============================================================================",
        f"# {ir.domain} — Environment Configuration",
        "# =============================================================================",
        "# Generated by Specora Forge. Copy to .env and customize.",
        "#",
        "# Secrets do NOT belong here. docker-compose.yml reads every credential",
        "# from ./secrets/ via the *_FILE convention, because anything set through",
        "# `environment:` or `env_file:` is readable with `docker inspect` and is",
        "# copied into crash reports. Run ./init-secrets.sh once to create them.",
        "#",
        "# Every variable below also accepts a <NAME>_FILE form pointing at a file,",
        "# which the entrypoint reads and exports. Setting both forms is an error.",
        "",
        "",
        "# =============================================================================",
        "# Database",
        "# =============================================================================",
        "",
        "# Left commented out on purpose. Under docker-compose the DSN — which",
        "# carries the database password — comes from secrets/database_url, and",
        "# setting both DATABASE_URL and DATABASE_URL_FILE is a boot failure by",
        "# design. Uncomment only for a deployment that is not using the compose",
        "# stack, and put a real password in it.",
        "# DATABASE_URL=postgresql://specora:CHANGEME@localhost:5432/specora",
        "DATABASE_BACKEND=postgres  # postgres | memory",
        "",
        "# Pool ceiling is this container's concurrency limit for database work.",
        "# Total connections are WEB_CONCURRENCY x DATABASE_POOL_MAX_SIZE per",
        "# replica; the database's max_connections is what runs out first.",
        "DATABASE_POOL_MIN_SIZE=2",
        "DATABASE_POOL_MAX_SIZE=10",
        "",
        "# Postgres cancels a statement that runs past this, freeing the connection.",
        "DATABASE_STATEMENT_TIMEOUT_MS=15000",
        "",
        "# How long entrypoint.sh waits for the database before failing the boot.",
        "# It fails immediately, without waiting, on a rejected password or a",
        "# missing database — those do not become true by retrying.",
        "DATABASE_WAIT_TIMEOUT_SECONDS=60",
        "",
        "",
        "# =============================================================================",
        "# Server",
        "# =============================================================================",
        "",
        "PORT=8000",
        "",
        "# Uvicorn worker processes. Unset means one. Memory and database",
        "# connections both scale linearly with this.",
        "WEB_CONCURRENCY=1",
        "",
        "# Seconds in-flight requests get after SIGTERM. Must stay below the",
        "# orchestrator's kill timeout (compose: stop_grace_period, 30s).",
        "UVICORN_GRACEFUL_TIMEOUT=20",
        "",
        "# Only set this behind a reverse proxy, and only to that proxy's address.",
        "# Uvicorn reads it directly. Left unset, X-Forwarded-* headers are ignored,",
        "# which is correct for anything a client can reach without passing through",
        "# a proxy you control.",
        "FORWARDED_ALLOW_IPS=",
        "",
        "# Comma-separated browser origins, e.g. https://app.example.com",
        "# The app refuses to boot on '*' unless CORS_ALLOW_CREDENTIALS=false.",
        "# Leave empty to allow no cross-origin browser access at all.",
        "CORS_ORIGINS=",
        "CORS_ALLOW_CREDENTIALS=true",
    ]

    if has_auth:
        lines.extend([
            "",
            "",
            "# =============================================================================",
            "# Authentication",
            "# =============================================================================",
            "",
            "# Under docker-compose this comes from secrets/auth_secret. Set it here",
            "# only on a platform that injects secrets as environment variables.",
            "# The app refuses to boot while it is empty, shorter than 32 characters,",
            "# or still the placeholder, because every token it issued would be",
            "# forgeable.  Generate one with: openssl rand -hex 32",
            "AUTH_SECRET=",
            "",
            "AUTH_PROVIDER=jwt  # jwt | external",
            "",
            "# Bound into every token and required on every token verified.",
            f"AUTH_ISSUER=specora:{ir.domain}",
            f"AUTH_AUDIENCE=specora:{ir.domain}",
            "",
            "# Access tokens cannot be revoked, so they are short-lived; the refresh",
            "# token is the revocable half and is single-use.",
            "AUTH_TOKEN_EXPIRE_MINUTES=15",
            "AUTH_REFRESH_TOKEN_EXPIRE_DAYS=14",
            "",
            "# The refresh token is also set as an httpOnly cookie. Browsers exempt",
            "# http://localhost from the Secure attribute, so only a deployment",
            "# deliberately served over plain HTTP needs this set to false.",
            "AUTH_COOKIE_SECURE=true",
            "",
            "# Authentication is declared by this domain's contracts. Setting this to",
            "# false does not disable it — the app refuses to boot instead.",
            "AUTH_ENABLED=true",
        ])

    lines.extend([
        "",
        "",
        "# =============================================================================",
        "# AI / LLM Providers (for Healer Tier 2-3 + Factory + Chat)",
        "# =============================================================================",
        "# At least one provider needed for LLM-powered self-healing.",
        "# Priority: SPECORA_AI_MODEL > ANTHROPIC > OPENAI > XAI > ZAI > OLLAMA",
        "#",
        "# These are billing credentials. Each also accepts the _FILE form, e.g.",
        "# ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key.",
        "",
        "# Override model: claude-sonnet-4-6, glm-5.1, gpt-4o, etc.",
        "SPECORA_AI_MODEL=",
        "",
        "# Anthropic (recommended) — https://console.anthropic.com/",
        "ANTHROPIC_API_KEY=",
        "",
        "# OpenAI — https://platform.openai.com/api-keys",
        "OPENAI_API_KEY=",
        "",
        "# xAI (Grok) — https://console.x.ai/",
        "XAI_API_KEY=",
        "",
        "# Z.AI (GLM) — https://z.ai — free models: glm-4.7-flash, glm-4.5-flash",
        "ZAI_API_KEY=",
        "",
        "# Local (Ollama) — https://ollama.com/",
        "OLLAMA_BASE_URL=",
        "",
        "",
        "# =============================================================================",
        "# Healer Service (runs as sidecar in Docker stack)",
        "# =============================================================================",
        "",
        "SPECORA_HEALER_PORT=8083",
        "",
        "# Where the app posts unhandled errors. docker-compose sets this to",
        "# http://healer:8083 over the compose network; port 8083 is deliberately",
        "# not published to the host, because the same port also serves the",
        "# control plane that applies contract fixes.",
        "SPECORA_HEALER_URL=",
        "",
        "# Data plane credential. Under docker-compose this comes from",
        "# secrets/healer_ingest_token. /healer/ingest is authenticated, and the",
        "# app logs a warning at startup if a URL is configured without a token,",
        "# because every report would be rejected.",
        "SPECORA_HEALER_INGEST_TOKEN=",
        "",
        "# Control plane credential: signs the approve/reject links, so holding it",
        "# is equivalent to being able to deploy. From secrets/healer_approval_secret",
        "# under docker-compose. Without one of this, SPECORA_HEALER_OPERATOR_TOKEN",
        "# or SPECORA_HEALER_PROXY_IDENTITY_HEADER the control plane fails closed.",
        "SPECORA_HEALER_APPROVAL_SECRET=",
        "",
        "# Static bearer token for operators fronting the Healer with their own",
        "# identity provider. Alternative to the signed-link scheme above.",
        "SPECORA_HEALER_OPERATOR_TOKEN=",
        "",
        "# Optional: POST notifications on state changes",
        "SPECORA_HEALER_WEBHOOK_URL=",
        "",
        "# Path to specora-core installation (for Healer Docker container)",
        "SPECORA_CORE_PATH=./../specora-core",
        "",
        "# The healer writes contracts and regenerated code back to bind mounts,",
        "# so its container uid must own this directory on the host.",
        "#   SPECORA_HEALER_UID=$(id -u)  SPECORA_HEALER_GID=$(id -g)",
        "SPECORA_HEALER_UID=1000",
        "SPECORA_HEALER_GID=1000",
        "",
        "",
        "# =============================================================================",
        "# Container limits and exposure",
        "# =============================================================================",
        "# Limits exist so one container cannot take the host down with it. The",
        "# defaults are a starting point; size them to the workload.",
        "",
        "APP_CPU_LIMIT=2",
        "APP_MEMORY_LIMIT=1g",
        "DB_CPU_LIMIT=2",
        "DB_MEMORY_LIMIT=1g",
        "HEALER_CPU_LIMIT=1",
        "HEALER_MEMORY_LIMIT=1g",
        "FRONTEND_CPU_LIMIT=1",
        "FRONTEND_MEMORY_LIMIT=512m",
        "",
        "# Published on loopback by default: the app speaks plain HTTP and expects",
        "# a TLS-terminating proxy in front of it. Widen deliberately.",
        "APP_BIND_ADDRESS=127.0.0.1",
        "FRONTEND_BIND_ADDRESS=127.0.0.1",
        "",
        "# Host-side ports. Only these two change; the in-container ports are",
        "# fixed, so nothing inside the stack has to be reconfigured to move them.",
        "APP_PORT=8000",
        "FRONTEND_PORT=3000",
        "",
        "# What the browser uses to reach the API. Inlined into the client bundle",
        "# at build time by Next.js, so changing it requires rebuilding the",
        "# frontend image, and it must not be a compose-internal hostname.",
        "NEXT_PUBLIC_API_URL=http://localhost:8000",
        "",
        "# Base image for both Python images. Pin a digest for a reproducible",
        "# build:  PYTHON_IMAGE=python@sha256:...",
        f"PYTHON_IMAGE={_PYTHON_IMAGE}",
        "",
    ])
    return GeneratedFile(
        path=".env.example", content="\n".join(lines), provenance=f"domain/{ir.domain}"
    )


# =============================================================================
# Requirements
# =============================================================================


def _generate_requirements(ir: DomainIR, has_auth: bool) -> GeneratedFile:
    # pydantic[email] pulls email-validator, which EmailStr imports at class
    # definition time — a model with an email field fails to import without it.
    needs_email = any(f.type == "email" for e in ir.entities for f in e.fields)
    pydantic = "pydantic[email]>=2.7,<3.0" if needs_email else "pydantic>=2.7,<3.0"

    deps = [
        "# Runtime dependencies of backend/. Nothing else belongs in the app image.",
        "fastapi>=0.115.3,<1.0",
        "uvicorn>=0.30,<1.0",
        pydantic,
        "asyncpg>=0.29,<0.31",
        "httpx>=0.27,<1.0",
    ]
    if has_auth:
        deps.extend([
            "",
            "# JWT: pyjwt rather than python-jose, which is unmaintained and carries",
            "# algorithm-confusion advisories. >=2.10 for the strict issuer check.",
            "pyjwt[crypto]>=2.10,<3.0",
            "# Password hashing: argon2-cffi directly rather than passlib, which is",
            "# unmaintained and whose 1.7.4 breaks against bcrypt 5.x.",
            "argon2-cffi>=23.1,<26.0",
        ])
    return GeneratedFile(
        path="requirements.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )


def _generate_dev_requirements(ir: DomainIR) -> GeneratedFile:
    # Deliberately a flat list rather than `-r requirements.txt`: every
    # generated requirements file is fed to pip-audit as a plain requirement
    # set, and an include directive there resolves against the wrong directory.
    deps = [
        "# Test-only. The runtime Dockerfile does not copy or install this file.",
        "# Install alongside the runtime set:",
        "#   pip install -r requirements.txt -r requirements-dev.txt",
        # 9.0.3 is the first release without PYSEC-2026-1845.
        "pytest>=9.0.3,<10.0",
    ]
    return GeneratedFile(
        path="requirements-dev.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )


def _generate_healer_requirements(ir: DomainIR) -> GeneratedFile:
    # Installed into Dockerfile.healer only. The healer runs specora-core's own
    # CLI, so it needs the CLI and LLM stack that the app image must not have.
    deps = [
        "fastapi>=0.115.3,<1.0",
        "uvicorn>=0.30,<1.0",
        "pydantic>=2.7,<3.0",
        "httpx>=0.27,<1.0",
        "pyyaml>=6.0.1,<7.0",
        "jsonschema>=4.23,<5.0",
        "click>=8.1.7,<9.0",
        "rich>=13.7,<15.0",
        "deepdiff>=7.0,<9.0",
        "python-dotenv>=1.0,<2.0",
        "prompt_toolkit>=3.0.47,<4.0",
        "# LLM providers for Tier 2-3 healing",
        "openai>=1.55,<3.0",
        "anthropic>=0.40,<1.0",
    ]
    return GeneratedFile(
        path="requirements.healer.txt",
        content="\n".join(deps) + "\n",
        provenance=f"domain/{ir.domain}",
    )
