"""Authentication and authorization for the Healer HTTP surface.

Why this module exists
======================

The Healer rewrites contracts — the source of truth from which the entire
application is generated — and then triggers regeneration. Approving a fix is
therefore a *code deployment*, not a comment on a bug tracker. It needs to be
authenticated to a human.

The deployment shape imposed by the product owner splits the surface into two
planes that get different exposure and different credentials:

Data plane (internal only)
--------------------------
``/healer/ingest``, ``/healer/status``, ``/healer/health``.

Reachable only from inside the deployment (bind to the internal interface, do
not publish the port). It still requires a shared secret,
``SPECORA_HEALER_INGEST_TOKEN``, because the app container already shares the
Healer's ``.env`` file: network position alone would mean any compromised
sibling container — or anything that can reach the compose network — could
inject unlimited tickets and drive unbounded LLM spend. Network isolation is
the outer wall; the token is the door lock. ``/healer/health`` is exempt so a
container orchestrator's health probe works without secrets, and it discloses
nothing beyond liveness.

Control plane (public)
----------------------
Ticket view, approve, reject.

Must be reachable from anywhere — an on-call engineer approves from a phone,
from a link in a Slack message — so it cannot rely on network position at all.
It authenticates an actor and records who approved in the diff audit trail.

Credential schemes
==================

Pluggable: an authenticator is any object with a ``name`` and an
``authenticate(credentials, ticket_id, action) -> Actor | None`` method.
Register one with :func:`register_control_plane_authenticator`; the chain is
built from the environment by :func:`build_control_plane_chain` and tried in
registration order. Three ship by default, each active only when its
environment variable is set:

``signed_action_token`` (``SPECORA_HEALER_APPROVAL_SECRET``)
    An HMAC-SHA256 token over ``(type, ticket_id, action, expiry, nonce)``.
    Self-contained: no session, no login page, no user database — which is what
    makes it fit the existing webhook notifier, which can simply put the URL in
    the Slack/Discord message. Approve and reject tokens are **single-use**:
    the nonce is consumed in the ticket database under a PRIMARY KEY constraint,
    so a replayed link (forwarded message, chat-client link preview fetcher,
    browser history) cannot re-apply a fix. View tokens are replayable until
    they expire, because the approve flow redirects back to the view page and a
    one-shot view token would break that round trip; a view token grants read
    of one ticket and cannot mutate anything.

``operator_token`` (``SPECORA_HEALER_OPERATOR_TOKEN``)
    A static bearer token. The fallback for operators who front the Healer with
    their own identity provider and want a simple machine credential behind it.

``proxy_identity`` (``SPECORA_HEALER_PROXY_IDENTITY_HEADER``)
    Trusts an identity header injected by an authenticating reverse proxy
    (Cloudflare Access, oauth2-proxy). Only safe when that proxy is the *only*
    route to the Healer and it strips any client-supplied copy of the header —
    otherwise anyone can set it. Disabled unless explicitly named.

If no authenticator is configured the control plane fails **closed** (503 with
a message naming the variables to set). There is no anonymous mode.

CSRF
====

The approve/reject HTML forms carry a CSRF token bound to
``(ticket_id, action, actor)``. This is not redundant with the action token:
the proxy_identity and operator schemes can be backed by a *cookie* set by the
fronting identity provider (Cloudflare Access does exactly this), and a cookie
is attached by the browser to a cross-site form POST. The CSRF token lives in
the form body, which a cross-origin page cannot read or forge.

Threats this does not address
=============================

- A stolen approval link before it is used, or a compromised operator token.
  Mitigated by expiry, single use, and the actor recorded in the audit trail —
  not prevented.
- Anyone with filesystem access to the contracts. The Healer is not the only
  writer.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Mapping, Optional, Protocol

logger = logging.getLogger(__name__)

INGEST_TOKEN_ENV = "SPECORA_HEALER_INGEST_TOKEN"
APPROVAL_SECRET_ENV = "SPECORA_HEALER_APPROVAL_SECRET"
OPERATOR_TOKEN_ENV = "SPECORA_HEALER_OPERATOR_TOKEN"
PROXY_IDENTITY_HEADER_ENV = "SPECORA_HEALER_PROXY_IDENTITY_HEADER"
ACTION_TOKEN_TTL_ENV = "SPECORA_HEALER_APPROVAL_TTL_SECONDS"
PUBLIC_URL_ENV = "SPECORA_HEALER_PUBLIC_URL"

# A notification lands in a chat channel and is acted on whenever the on-call
# engineer next looks at it, which may be the next morning.
DEFAULT_ACTION_TOKEN_TTL = 86_400
CSRF_TTL_SECONDS = 3_600

ACTION_VIEW = "view"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_LIST = "list"

# Actions that change contracts on disk. Their tokens are burned on use.
MUTATING_ACTIONS = frozenset({ACTION_APPROVE, ACTION_REJECT})

_TOKEN_VERSION = "shv1"
_TYPE_ACTION = "action"
_TYPE_CSRF = "csrf"


class AuthError(Exception):
    """Authentication or authorization failure carrying an HTTP status."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Actor:
    """An authenticated principal on the control plane.

    ``scope`` is set when the credential authorizes exactly one
    ``(ticket_id, action)`` pair — a signed action token — and is None for
    broad credentials such as an operator bearer token.
    """

    kind: str
    id: str
    scope: Optional[tuple[str, str]] = None
    nonce: str = ""

    @property
    def audit_id(self) -> str:
        """Stable identifier written into the diff audit trail."""
        return f"{self.kind}:{self.id}"

    @property
    def principal(self) -> str:
        """Who this credential ultimately represents.

        A signed link carries the identity it was minted for in its subject, so
        the same person keeps one identity across the GET that rendered the
        page (authenticated by, say, an identity proxy) and the POST that
        submitted the form (authenticated by the link token minted for them).
        CSRF binding uses this rather than :attr:`audit_id`, which changes with
        the credential scheme.
        """
        return self.id if self.kind == "approval_link" else self.audit_id


@dataclass(frozen=True)
class Credentials:
    """Transport-agnostic view of the credentials on one request.

    Kept free of FastAPI types so the authenticators can be unit-tested and so
    a non-HTTP caller (the CLI) can build one directly.
    """

    bearer: str = ""
    query_token: str = ""
    form_token: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return ""

    def candidate_tokens(self) -> list[str]:
        """Every place a signed token may arrive, most specific first."""
        return [t for t in (self.form_token, self.query_token, self.bearer) if t]


# ---------------------------------------------------------------------------
# Signed token codec
# ---------------------------------------------------------------------------


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def _sign(secret: str, message: str) -> str:
    return _b64e(hmac.new(secret.encode("utf-8"), message.encode("utf-8"), sha256).digest())


def _approval_secret(secret: Optional[str] = None) -> str:
    resolved = secret if secret is not None else os.environ.get(APPROVAL_SECRET_ENV, "")
    if not resolved:
        raise AuthError(
            503,
            f"Signed approval tokens are not configured. Set {APPROVAL_SECRET_ENV}.",
        )
    return resolved


def _issue(
    typ: str,
    ticket_id: str,
    action: str,
    *,
    secret: str,
    ttl_seconds: int,
    subject: str = "",
) -> str:
    claims = {
        "typ": typ,
        "tid": ticket_id,
        "act": action,
        "exp": int(time.time()) + int(ttl_seconds),
        "nonce": secrets.token_urlsafe(12),
    }
    if subject:
        claims["sub"] = subject
    payload = _b64e(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    signed = f"{_TOKEN_VERSION}.{payload}"
    return f"{signed}.{_sign(secret, signed)}"


def _verify(
    token: str,
    typ: str,
    ticket_id: str,
    action: str,
    *,
    secret: str,
) -> dict:
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_VERSION:
        raise AuthError(401, "Malformed token.")
    version, payload, signature = parts
    expected = _sign(secret, f"{version}.{payload}")
    if not hmac.compare_digest(expected, signature):
        raise AuthError(401, "Invalid token signature.")
    try:
        claims = json.loads(_b64d(payload))
    except (ValueError, binascii.Error) as exc:
        raise AuthError(401, "Malformed token payload.") from exc
    if not isinstance(claims, dict):
        raise AuthError(401, "Malformed token payload.")
    # Domain separation: a view token must never be spendable as an approval,
    # and a CSRF token must never be spendable as either.
    if claims.get("typ") != typ:
        raise AuthError(401, "Token is not valid for this purpose.")
    if claims.get("tid") != ticket_id:
        raise AuthError(401, "Token is not valid for this ticket.")
    if claims.get("act") != action:
        raise AuthError(401, "Token is not valid for this action.")
    if int(claims.get("exp", 0)) <= int(time.time()):
        raise AuthError(401, "Token has expired.")
    return claims


def issue_action_token(
    ticket_id: str,
    action: str,
    *,
    secret: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
    subject: str = "",
) -> str:
    """Mint a signed, expiring token authorizing one action on one ticket."""
    resolved_ttl = ttl_seconds if ttl_seconds is not None else _configured_ttl()
    return _issue(
        _TYPE_ACTION,
        ticket_id,
        action,
        secret=_approval_secret(secret),
        ttl_seconds=resolved_ttl,
        subject=subject,
    )


def verify_action_token(
    token: str,
    ticket_id: str,
    action: str,
    *,
    secret: Optional[str] = None,
) -> dict:
    """Verify a signed action token. Raises :class:`AuthError` on any failure."""
    return _verify(token, _TYPE_ACTION, ticket_id, action, secret=_approval_secret(secret))


def _configured_ttl() -> int:
    raw = os.environ.get(ACTION_TOKEN_TTL_ENV, "")
    if not raw:
        return DEFAULT_ACTION_TOKEN_TTL
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer (%r); using default TTL", ACTION_TOKEN_TTL_ENV, raw)
        return DEFAULT_ACTION_TOKEN_TTL
    return value if value > 0 else DEFAULT_ACTION_TOKEN_TTL


def approval_tokens_enabled() -> bool:
    """True when signed action tokens can be minted and verified."""
    return bool(os.environ.get(APPROVAL_SECRET_ENV, ""))


def looks_like_signed_token(token: str) -> bool:
    """True when the value claims to be one of our tokens.

    Lets the authenticator report "your token is expired" instead of silently
    falling through to the next scheme and reporting a generic 401.
    """
    return token.startswith(f"{_TOKEN_VERSION}.")


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------

# Falls back to a per-process secret so CSRF still works on a Healer configured
# only with proxy identity. A restart invalidates outstanding forms, which is
# acceptable for a one-hour token, and multi-worker deployments must set one of
# the real secrets.
_EPHEMERAL_CSRF_SECRET = secrets.token_urlsafe(32)


def _csrf_secret() -> str:
    return (
        os.environ.get(APPROVAL_SECRET_ENV, "")
        or os.environ.get(OPERATOR_TOKEN_ENV, "")
        or _EPHEMERAL_CSRF_SECRET
    )


def issue_csrf_token(ticket_id: str, action: str, actor: Actor) -> str:
    return _issue(
        _TYPE_CSRF,
        ticket_id,
        action,
        secret=_csrf_secret(),
        ttl_seconds=CSRF_TTL_SECONDS,
        subject=actor.principal,
    )


def verify_csrf_token(token: str, ticket_id: str, action: str, actor: Actor) -> None:
    if not token:
        raise AuthError(403, "Missing CSRF token.")
    claims = _verify(token, _TYPE_CSRF, ticket_id, action, secret=_csrf_secret())
    if not hmac.compare_digest(str(claims.get("sub", "")), actor.principal):
        raise AuthError(403, "CSRF token was issued to a different actor.")


# ---------------------------------------------------------------------------
# Data plane
# ---------------------------------------------------------------------------


def require_ingest_token(credentials: Credentials) -> Actor:
    """Authenticate a data-plane caller (the generated app, the watcher)."""
    configured = os.environ.get(INGEST_TOKEN_ENV, "")
    if not configured:
        raise AuthError(
            503,
            f"Healer data plane is not configured. Set {INGEST_TOKEN_ENV} on the "
            "Healer and on every service that reports errors to it.",
        )
    presented = credentials.bearer or credentials.header("X-Specora-Healer-Token")
    if not presented or not hmac.compare_digest(presented, configured):
        raise AuthError(401, "Invalid or missing Healer data-plane token.")
    return Actor(kind="service", id="data-plane")


# ---------------------------------------------------------------------------
# Control plane authenticators
# ---------------------------------------------------------------------------


class ControlPlaneAuthenticator(Protocol):
    """A credential scheme for the public control plane."""

    name: str

    def authenticate(
        self, credentials: Credentials, ticket_id: str, action: str
    ) -> Optional[Actor]:
        """Return an Actor, None to defer to the next scheme, or raise AuthError."""


class SignedActionTokenAuthenticator:
    """Single-use, expiring HMAC tokens scoped to one ticket and action."""

    name = "signed_action_token"

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def authenticate(
        self, credentials: Credentials, ticket_id: str, action: str
    ) -> Optional[Actor]:
        for token in credentials.candidate_tokens():
            if not looks_like_signed_token(token):
                continue
            claims = verify_action_token(token, ticket_id, action, secret=self._secret)
            subject = str(claims.get("sub") or "link")
            return Actor(
                kind="approval_link",
                id=subject,
                scope=(ticket_id, action),
                nonce=str(claims.get("nonce", "")),
            )
        return None


class OperatorTokenAuthenticator:
    """Static bearer token for operators fronting the Healer with their own IdP."""

    name = "operator_token"

    def __init__(self, token: str) -> None:
        self._token = token

    def authenticate(
        self, credentials: Credentials, ticket_id: str, action: str
    ) -> Optional[Actor]:
        presented = credentials.bearer or credentials.header("X-Specora-Operator-Token")
        if not presented or looks_like_signed_token(presented):
            return None
        if not hmac.compare_digest(presented, self._token):
            return None
        return Actor(kind="operator", id="static-token")


class ProxyIdentityAuthenticator:
    """Trusts an identity header set by an authenticating reverse proxy."""

    name = "proxy_identity"

    def __init__(self, header_name: str) -> None:
        self._header_name = header_name

    def authenticate(
        self, credentials: Credentials, ticket_id: str, action: str
    ) -> Optional[Actor]:
        identity = credentials.header(self._header_name).strip()
        if not identity:
            return None
        return Actor(kind="proxy", id=identity)


AuthenticatorFactory = Callable[[], Optional[ControlPlaneAuthenticator]]
_REGISTRY: dict[str, AuthenticatorFactory] = {}


def register_control_plane_authenticator(name: str, factory: AuthenticatorFactory) -> None:
    """Register a credential scheme.

    ``factory`` returns None when the scheme is not configured, which keeps
    every scheme opt-in and the whole plane fail-closed by default.
    """
    _REGISTRY[name] = factory


def _signed_token_factory() -> Optional[ControlPlaneAuthenticator]:
    secret = os.environ.get(APPROVAL_SECRET_ENV, "")
    return SignedActionTokenAuthenticator(secret) if secret else None


def _operator_token_factory() -> Optional[ControlPlaneAuthenticator]:
    token = os.environ.get(OPERATOR_TOKEN_ENV, "")
    return OperatorTokenAuthenticator(token) if token else None


def _proxy_identity_factory() -> Optional[ControlPlaneAuthenticator]:
    header = os.environ.get(PROXY_IDENTITY_HEADER_ENV, "")
    return ProxyIdentityAuthenticator(header) if header else None


register_control_plane_authenticator("signed_action_token", _signed_token_factory)
register_control_plane_authenticator("operator_token", _operator_token_factory)
register_control_plane_authenticator("proxy_identity", _proxy_identity_factory)


def build_control_plane_chain() -> list[ControlPlaneAuthenticator]:
    """Build the active authenticator chain from the environment."""
    chain: list[ControlPlaneAuthenticator] = []
    for factory in _REGISTRY.values():
        authenticator = factory()
        if authenticator is not None:
            chain.append(authenticator)
    return chain


def authorize_control_plane(
    credentials: Credentials,
    ticket_id: str,
    action: str,
) -> Actor:
    """Authenticate a control-plane request. Raises :class:`AuthError` on failure."""
    chain = build_control_plane_chain()
    if not chain:
        raise AuthError(
            503,
            "Healer control plane has no credential scheme configured. Set at "
            f"least one of {APPROVAL_SECRET_ENV}, {OPERATOR_TOKEN_ENV}, "
            f"{PROXY_IDENTITY_HEADER_ENV}.",
        )
    for authenticator in chain:
        actor = authenticator.authenticate(credentials, ticket_id, action)
        if actor is not None:
            return actor
    raise AuthError(401, "Authentication required.")


def public_base_url(default_port: str = "8083") -> str:
    """Base URL that appears in notification links."""
    configured = os.environ.get(PUBLIC_URL_ENV, "").rstrip("/")
    if configured:
        return configured
    port = os.environ.get("SPECORA_HEALER_PORT", default_port)
    return f"http://localhost:{port}"
