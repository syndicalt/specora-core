"""Tests for the container artefacts.

Every assertion here corresponds to something that was measured wrong on
freshly generated output, or to a control that was verified working against a
booted stack and must not silently regress:

  * the application container ran as root, with no ``USER`` directive at all
  * neither image had a ``HEALTHCHECK``, so compose could not tell a wedged
    container from a healthy one
  * docker-compose.yml carried one occurrence of
    healthcheck/restart/read_only/cap_drop/user across the whole file
  * every credential was passed as a plain environment variable, readable with
    ``docker inspect``
  * the database wait retried a rejected password forever, presenting a
    permanently broken deployment as a container that was merely slow to start
"""

from __future__ import annotations

import ipaddress

import yaml

from forge.ir.model import DomainIR, EntityIR, FieldIR, InfraIR
from forge.targets.fastapi_prod.gen_docker import generate_docker

# Services that run in the deployed stack. `frontend` is behind a compose
# profile but is still part of the deployment when it is enabled.
_ALL_SERVICES = ("db", "app", "healer", "frontend")


def _ir(*, auth: bool = False) -> DomainIR:
    ir = DomainIR(domain="shop")
    ir.entities = [
        EntityIR(
            fqn="entity/shop/product",
            name="product",
            domain="shop",
            table_name="products",
            fields=[FieldIR(name="name", type="string", required=True)],
        )
    ]
    if auth:
        ir.infra = [
            InfraIR(fqn="infra/shop/auth", name="auth", domain="shop", category="auth", config={})
        ]
    return ir


def _files(*, auth: bool = False) -> dict[str, str]:
    return {f.path: f.content for f in generate_docker(ir=_ir(auth=auth))}


def _compose(*, auth: bool = False) -> dict:
    return yaml.safe_load(_files(auth=auth)["docker-compose.yml"])


class TestApplicationImage:
    def test_runs_as_a_fixed_non_root_uid(self) -> None:
        dockerfile = _files()["Dockerfile"]
        assert "USER 10001:10001" in dockerfile
        assert "--uid 10001" in dockerfile

    def test_has_a_healthcheck_against_the_health_endpoint(self) -> None:
        dockerfile = _files()["Dockerfile"]
        assert "HEALTHCHECK" in dockerfile
        assert "/health" in dockerfile
        # Emitted on one line: a `\` continuation would land inside the quoted
        # Python snippet the healthcheck runs.
        (line,) = [ln for ln in dockerfile.splitlines() if ln.strip().startswith("CMD python")]
        assert not line.endswith("\\")

    def test_application_code_is_not_writable_by_the_serving_process(self) -> None:
        # No `--chown` on the source COPYs: the code stays root-owned so the
        # unprivileged runtime user cannot rewrite what it is executing.
        dockerfile = _files()["Dockerfile"]
        assert "COPY backend/ backend/" in dockerfile
        assert "COPY database/ database/" in dockerfile
        assert "--chown" not in dockerfile

    def test_the_c_toolchain_does_not_reach_the_runtime_image(self) -> None:
        dockerfile = _files()["Dockerfile"]
        assert "AS builder" in dockerfile
        assert "AS runtime" in dockerfile
        builder, runtime = dockerfile.split("FROM ${PYTHON_IMAGE} AS runtime")
        assert "pip install" in builder
        assert "build-essential" in builder
        assert "pip install" not in runtime
        assert "build-essential" not in runtime

    def test_base_image_is_a_build_arg_so_a_digest_can_be_pinned(self) -> None:
        assert "ARG PYTHON_IMAGE=" in _files()["Dockerfile"]

    def test_the_test_framework_is_not_installed_into_the_runtime_image(self) -> None:
        files = _files()
        assert "pytest" in files["requirements-dev.txt"]
        assert "pytest" not in files["requirements.txt"]
        assert "requirements-dev.txt" not in files["Dockerfile"]


class TestHealerImage:
    def test_runs_as_a_fixed_non_root_uid(self) -> None:
        assert "USER 10002:10002" in _files()["Dockerfile.healer"]

    def test_has_a_healthcheck_against_the_unauthenticated_health_endpoint(self) -> None:
        dockerfile = _files()["Dockerfile.healer"]
        assert "HEALTHCHECK" in dockerfile
        assert "/healer/health" in dockerfile


class TestEntrypoint:
    def test_resolves_file_backed_secrets_before_starting(self) -> None:
        entrypoint = _files(auth=True)["entrypoint.sh"]
        resolve = [ln for ln in entrypoint.splitlines() if ln.startswith("resolve_file_secrets ")]
        assert resolve == [
            "resolve_file_secrets AUTH_SECRET DATABASE_URL SPECORA_HEALER_INGEST_TOKEN"
        ]

    def test_does_not_ask_for_a_signing_secret_a_domain_without_auth_never_reads(self) -> None:
        assert "AUTH_SECRET" not in _files(auth=False)["entrypoint.sh"]

    def test_setting_both_a_secret_and_its_file_is_an_error(self) -> None:
        # Silently preferring one would leave half of all operators with a
        # deployment running on the credential they did not set.
        assert "are set; pick one." in _files()["entrypoint.sh"]

    def test_the_database_wait_is_bounded(self) -> None:
        entrypoint = _files()["entrypoint.sh"]
        assert "DATABASE_WAIT_TIMEOUT_SECONDS" in entrypoint
        assert "Database not reachable within" in entrypoint

    def test_the_database_wait_fails_fast_on_errors_waiting_cannot_fix(self) -> None:
        entrypoint = _files()["entrypoint.sh"]
        for permanent in (
            "InvalidPasswordError",
            "InvalidCatalogNameError",
            "InvalidAuthorizationSpecificationError",
        ):
            assert permanent in entrypoint

    def test_the_database_wait_does_not_discard_the_error_it_failed_on(self) -> None:
        # The previous implementation sent stderr to /dev/null, which is what
        # turned every cause into the same silent hang.
        assert "2>/dev/null" not in _files()["entrypoint.sh"]

    def test_uvicorn_replaces_the_shell_so_it_receives_sigterm(self) -> None:
        entrypoint = _files()["entrypoint.sh"]
        assert "exec uvicorn" in entrypoint
        assert "--timeout-graceful-shutdown" in entrypoint

    def test_forwarded_headers_are_not_trusted_by_default(self) -> None:
        # Trusting them from an arbitrary peer lets any client claim any source
        # address. uvicorn reads FORWARDED_ALLOW_IPS itself when it is set.
        assert "--forwarded-allow-ips" not in _files()["entrypoint.sh"]


class TestComposeHardening:
    def test_every_service_drops_all_capabilities(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            assert services[name]["cap_drop"] == ["ALL"], name

    def test_every_service_forbids_privilege_escalation(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            assert services[name]["security_opt"] == ["no-new-privileges:true"], name

    def test_every_service_has_a_read_only_root_filesystem(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            assert services[name]["read_only"] is True, name
            # A read-only root with no writable path named is a container that
            # cannot boot. Each one has to declare where it may write.
            assert services[name]["tmpfs"], name

    def test_every_service_is_bounded_in_cpu_and_memory(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            limits = services[name]["deploy"]["resources"]["limits"]
            assert limits["cpus"], name
            assert limits["memory"], name

    def test_every_service_rotates_its_logs(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            logging = services[name]["logging"]
            assert logging["driver"] == "json-file", name
            assert logging["options"]["max-size"], name
            assert logging["options"]["max-file"], name

    def test_every_service_restarts_on_failure(self) -> None:
        services = _compose()["services"]
        for name in _ALL_SERVICES:
            assert services[name]["restart"] == "unless-stopped", name

    def test_the_database_runs_as_the_postgres_uid_not_root(self) -> None:
        # cap_drop: ALL removes the CAP_SETUID the official entrypoint needs to
        # step down from root, so it must not start as root in the first place.
        assert _compose()["services"]["db"]["user"] == "999:999"

    def test_the_app_waits_for_a_healthy_database_not_merely_a_started_one(self) -> None:
        assert _compose()["services"]["app"]["depends_on"]["db"]["condition"] == "service_healthy"

    def test_the_app_does_not_block_on_its_observability_sidecar(self) -> None:
        # A degraded feedback loop must not become a failed deploy.
        assert "healer" not in _compose()["services"]["app"].get("depends_on", {})

    def test_stop_grace_period_exceeds_the_graceful_shutdown_timeout(self) -> None:
        # Otherwise the difference is spent being SIGKILLed mid-request.
        assert _compose()["services"]["app"]["stop_grace_period"] == "30s"

    def test_the_database_healthcheck_names_the_database_it_is_probing(self) -> None:
        # Bare `pg_isready` probes the current OS user's default database and
        # reports success before this one exists.
        test = _compose()["services"]["db"]["healthcheck"]["test"]
        assert "-U specora -d specora" in test[-1]


class TestComposeExposure:
    def test_the_database_port_is_not_published(self) -> None:
        assert "ports" not in _compose()["services"]["db"]

    def test_the_healer_port_is_not_published(self) -> None:
        # The same port serves the control plane that applies contract fixes.
        assert "ports" not in _compose()["services"]["healer"]

    def test_the_app_publishes_on_loopback_by_default(self) -> None:
        (published,) = _compose()["services"]["app"]["ports"]
        assert published.startswith("${APP_BIND_ADDRESS:-127.0.0.1}:")


class TestComposeSecrets:
    def test_no_credential_is_passed_as_a_literal_environment_value(self) -> None:
        # Anything under `environment:` is readable with `docker inspect`.
        compose = _compose(auth=True)
        for name, service in compose["services"].items():
            for key, value in (service.get("environment") or {}).items():
                if not any(t in key for t in ("SECRET", "TOKEN", "PASSWORD", "DATABASE_URL")):
                    continue
                assert key.endswith("_FILE"), f"{name}.{key} is not file-backed"
                assert str(value).startswith("/run/secrets/"), f"{name}.{key}"

    def test_every_referenced_secret_is_declared(self) -> None:
        compose = _compose(auth=True)
        declared = set(compose["secrets"])
        for name, service in compose["services"].items():
            for used in service.get("secrets") or []:
                assert used in declared, f"{name} mounts undeclared secret {used}"

    def test_the_signing_secret_is_only_wired_where_the_domain_declares_auth(self) -> None:
        assert "auth_secret" in _compose(auth=True)["secrets"]
        assert "auth_secret" not in _compose(auth=False)["secrets"]

    def test_no_service_carries_a_default_credential(self) -> None:
        # A stack that boots with a built-in password is a stack that reaches
        # production with one.
        compose_text = _files(auth=True)["docker-compose.yml"]
        assert "POSTGRES_PASSWORD:" not in compose_text
        assert "POSTGRES_PASSWORD_FILE:" in compose_text

    def test_the_secret_generator_never_overwrites_an_existing_credential(self) -> None:
        # Re-running it after adding a service must not rotate live keys.
        assert "already exists" in _files()["init-secrets.sh"]

    def test_the_build_context_excludes_the_secret_directory(self) -> None:
        dockerignore = _files()[".dockerignore"]
        assert "secrets/" in dockerignore
        assert ".env\n" in dockerignore


class TestComposeFrontend:
    def test_the_api_url_is_a_build_arg_because_next_inlines_it_at_build_time(self) -> None:
        # As a runtime environment variable it has no effect on the bundle the
        # browser executes.
        frontend = _compose()["services"]["frontend"]
        assert "NEXT_PUBLIC_API_URL" in frontend["build"]["args"]
        assert "NEXT_PUBLIC_API_URL" not in (frontend.get("environment") or {})

    def test_the_api_url_default_is_reachable_from_a_browser(self) -> None:
        # `http://app:8000` resolves only inside the compose network.
        default = _compose()["services"]["frontend"]["build"]["args"]["NEXT_PUBLIC_API_URL"]
        assert "app:8000" not in default

    def test_the_server_binds_where_its_healthcheck_probes(self) -> None:
        # Next.js standalone binds to $HOSTNAME, which inside a container
        # resolves to the eth0 address alone — leaving loopback unserved.
        frontend = _compose()["services"]["frontend"]
        bind = ipaddress.ip_address(frontend["environment"]["HOSTNAME"])
        assert bind.is_unspecified, f"binds {bind}, so loopback goes unserved"
        assert "127.0.0.1:3000" in " ".join(frontend["healthcheck"]["test"])


class TestEnvExample:
    def test_it_does_not_ship_a_usable_placeholder_secret(self) -> None:
        # backend/config.py refuses to boot on the old placeholder value, and
        # the two must not drift back into agreement.
        env = _files(auth=True)[".env.example"]
        assert "AUTH_SECRET=\n" in env or env.endswith("AUTH_SECRET=")
        assert "change-me-in-production" not in env

    def test_it_documents_the_healer_control_plane_credentials(self) -> None:
        # Without one of these the control plane fails closed, and nothing
        # previously told an operator they existed.
        env = _files()[".env.example"]
        assert "SPECORA_HEALER_APPROVAL_SECRET" in env
        assert "SPECORA_HEALER_OPERATOR_TOKEN" in env

    def test_it_points_operators_at_the_file_based_convention(self) -> None:
        assert "_FILE" in _files()[".env.example"]

    def test_it_sets_nothing_the_compose_secrets_also_supply(self) -> None:
        # `spc forge generate` copies this file to .env, compose loads .env into
        # the app container, and setting both a secret and its _FILE form is a
        # boot failure. A shipped DATABASE_URL therefore broke `up` outright.
        compose = _compose(auth=True)
        file_backed = {
            key[: -len("_FILE")]
            for service in compose["services"].values()
            for key in (service.get("environment") or {})
            if key.endswith("_FILE")
        }
        assigned = {
            line.split("=", 1)[0]
            for line in _files(auth=True)[".env.example"].splitlines()
            if "=" in line and not line.startswith("#") and line.split("=", 1)[1].strip()
        }
        assert not (file_backed & assigned)
