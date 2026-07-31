"""Generate the frontend's session layer: token custody, gate, and sign-in page.

Emitted only when the domain declares an `infra/<domain>/auth` contract. The
API generator puts `require_auth`/`require_role` on every endpoint of such a
domain, so without this the generated frontend gets a 401 on every call and,
because it had no 401 handler, rendered an empty page instead of saying so.

The token-storage decision is documented at the top of the generated
`session.ts`, where the person maintaining the app will actually read it.
"""

from __future__ import annotations

from forge.ir.model import DomainIR
from forge.targets.base import GeneratedFile, provenance_header
from forge.targets.nextjs.context import AuthSpec, FrontendContext


def generate_session(ctx: FrontendContext) -> list[GeneratedFile]:
    """Generate the session module, the route gate, and the sign-in page."""
    if ctx.auth is None:
        return []
    return [
        _generate_session_module(ctx.ir, ctx.auth),
        _generate_app_shell(ctx.ir, ctx.auth),
        _generate_login_page(ctx.ir, ctx.auth),
    ]


def _generate_session_module(ir: DomainIR, auth: AuthSpec) -> GeneratedFile:
    header = provenance_header("typescript", auth.fqn, "Session and token custody")
    content = (
        header.rstrip()
        + f"""

"use client";

import {{ API_BASE, LOGIN_ROUTE }} from "./config";

/**
 * Where the tokens live, and why.
 *
 * The ACCESS token is held in this module's closure and nowhere else. It is
 * never written to localStorage, sessionStorage, a cookie, or the URL, so it
 * does not survive a reload, cannot be recovered by script injected into a
 * later page load, and never leaks through a Referer header or a log line.
 * Every page load starts by trading the refresh token for a new one.
 *
 * The REFRESH token has two possible homes, and which one is in use is
 * detected at sign-in rather than configured:
 *
 *   1. PREFERRED — an httpOnly, Secure, SameSite=Lax cookie set by the API.
 *      Script on this origin cannot read it at all, so an XSS cannot exfiltrate
 *      a durable credential. Nothing is written to web storage in this mode.
 *
 *   2. FALLBACK — sessionStorage, used only when the API does not set that
 *      cookie. sessionStorage is scoped to one tab and is discarded when the
 *      tab closes; localStorage is never used, because it survives browser
 *      restarts and is shared by every tab, so a token stolen once from it
 *      stays usable indefinitely and everywhere.
 *
 * The probe that chooses between them is a refresh attempt with no body,
 * issued once immediately after sign-in. If the API is holding the cookie the
 * attempt succeeds and nothing is ever persisted; if it is not, the attempt is
 * rejected by request validation before any token is spent, and mode 2 is used
 * from then on.
 *
 * In mode 2 the refresh token is readable by script running on this origin, so
 * an XSS wins for as long as it runs — no client-side store changes that. What
 * bounds the damage is server-side and already implemented: refresh tokens
 * rotate on every use and a replayed one revokes the whole family, so a stolen
 * token either races the legitimate client and is detected, or is invalidated
 * the next time the legitimate client refreshes.
 */

/** Mirrors `TokenPair` in backend/auth/interface.py. */
interface TokenPair {{
  access_token: string;
  refresh_token?: string;
  token_type: string;
}}

export type SignInResult = {{ ok: true }} | {{ ok: false; message: string }};

const REFRESH_KEY = "specora.{ir.domain}.refresh";

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

/**
 * Whether the API keeps the refresh token in an httpOnly cookie.
 *
 * `null` means not yet determined — after a reload, before the first refresh.
 */
let cookieRefresh: boolean | null = null;

const listeners = new Set<() => void>();

/** Subscribe to sign-in/sign-out, so React can re-render on a session change. */
export function subscribe(listener: () => void): () => void {{
  listeners.add(listener);
  return () => {{
    listeners.delete(listener);
  }};
}}

function notify(): void {{
  listeners.forEach((listener) => listener());
}}

function readRefreshToken(): string | null {{
  if (typeof window === "undefined") return null;
  try {{
    return window.sessionStorage.getItem(REFRESH_KEY);
  }} catch {{
    // Storage throws outright when it is blocked by policy or by private
    // browsing. No stored token is the correct answer, not an error.
    return null;
  }}
}}

function writeRefreshToken(token: string | null): void {{
  if (typeof window === "undefined") return;
  try {{
    if (token === null) window.sessionStorage.removeItem(REFRESH_KEY);
    else window.sessionStorage.setItem(REFRESH_KEY, token);
  }} catch {{
    // Same as above: the session then lasts until the next reload rather than
    // the sign-in failing outright.
  }}
}}

/** The Authorization header for an outgoing request, or nothing when signed out. */
export function authorizationHeader(): Record<string, string> {{
  return accessToken === null ? {{}} : {{ Authorization: `Bearer ${{accessToken}}` }};
}}

/** Whether a usable access token is already in hand. */
export function hasAccessToken(): boolean {{
  return accessToken !== null;
}}

/** Drop every credential without navigating. */
export function clearSession(): void {{
  accessToken = null;
  writeRefreshToken(null);
  notify();
}}

/**
 * Post to /auth/refresh and adopt the result.
 *
 * `credentials: "include"` is what carries the httpOnly cookie when the API
 * sets one. Omitting `refresh_token` from the body is what forces the server
 * to use that cookie; a server that has no cookie to read rejects the request
 * at validation, before any stored token is spent.
 */
async function postRefresh(token: string | null): Promise<TokenPair | null> {{
  const res = await fetch(`${{API_BASE}}/auth/refresh`, {{
    method: "POST",
    credentials: "include",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify(token === null ? {{}} : {{ refresh_token: token }}),
  }});
  if (!res.ok) return null;
  return (await res.json()) as TokenPair;
}}

/**
 * Spend the refresh token for a new pair. Resolves false when the session is
 * over and the caller should stop retrying.
 */
export function refreshSession(): Promise<boolean> {{
  // Single-flight. A page issuing four requests at once would otherwise spend
  // the same refresh token four times, and the server treats the second use as
  // a replay and revokes the entire family — logging the user out for being
  // busy.
  if (refreshInFlight !== null) return refreshInFlight;

  const stored = readRefreshToken();
  const probeCookie = cookieRefresh !== false;
  if (!probeCookie && stored === null) return Promise.resolve(false);

  refreshInFlight = (async () => {{
    try {{
      if (probeCookie) {{
        const viaCookie = await postRefresh(null);
        if (viaCookie !== null) {{
          cookieRefresh = true;
          accessToken = viaCookie.access_token;
          // The cookie is authoritative in this mode, so the copy in the body
          // is deliberately dropped rather than persisted.
          writeRefreshToken(null);
          notify();
          return true;
        }}
        cookieRefresh = false;
      }}

      if (stored === null) {{
        clearSession();
        return false;
      }}

      const viaBody = await postRefresh(stored);
      if (viaBody === null) {{
        clearSession();
        return false;
      }}
      accessToken = viaBody.access_token;
      writeRefreshToken(viaBody.refresh_token ?? null);
      notify();
      return true;
    }} catch (cause) {{
      // A transport failure is not evidence the session is dead, so the
      // refresh token is kept and a later attempt can still succeed.
      console.error("Token refresh could not be sent", cause);
      return false;
    }} finally {{
      refreshInFlight = null;
    }}
  }})();

  return refreshInFlight;
}}

/** Exchange credentials for a token pair. */
export async function signIn(
  identity: string,
  password: string,
): Promise<SignInResult> {{
  let res: Response;
  try {{
    res = await fetch(`${{API_BASE}}/auth/login`, {{
      method: "POST",
      credentials: "include",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ {auth.identity_field}: identity, password }}),
    }});
  }} catch (cause) {{
    console.error("Sign-in could not be sent", cause);
    return {{ ok: false, message: "Could not reach the server. Check your connection." }};
  }}

  if (res.ok) {{
    const pair = (await res.json()) as TokenPair;
    accessToken = pair.access_token;

    // Decide the refresh token's home before persisting anything. If the API
    // set an httpOnly cookie this bodyless refresh succeeds and the token
    // never touches web storage; if it did not, the attempt is rejected by
    // request validation without spending the token we are still holding.
    const viaCookie = await postRefresh(null);
    if (viaCookie !== null) {{
      cookieRefresh = true;
      accessToken = viaCookie.access_token;
      writeRefreshToken(null);
    }} else {{
      cookieRefresh = false;
      writeRefreshToken(pair.refresh_token ?? null);
    }}

    notify();
    return {{ ok: true }};
  }}

  // The server's own text is never shown: on a 500 it is the stringified
  // Python exception.
  console.error("Sign-in rejected", res.status, await res.text());
  return {{ ok: false, message: signInMessage(res.status) }};
}}

function signInMessage(status: number): string {{
  if (status === 401) return "That {auth.identity_label.lower()} or password was not recognised.";
  if (status === 404) {{
    // The API only exposes /auth/login when its auth contract names a
    // credential store (`config.user_entity`).
    return "Sign-in is not available on this deployment.";
  }}
  if (status === 422) return "Enter a valid {auth.identity_label.lower()} and password.";
  if (status === 429) return "Too many attempts. Wait a moment and try again.";
  return "Sign-in failed. Try again shortly.";
}}

/**
 * Only a same-origin path is a safe post-sign-in destination.
 *
 * `next=//evil.example` and `next=/\\evil.example` are both protocol-relative
 * to a browser, so a bare "starts with /" check is an open redirect.
 */
export function safeNext(raw: string | null): string {{
  if (!raw) return "/";
  if (!raw.startsWith("/")) return "/";
  if (raw.startsWith("//") || raw.startsWith("/\\\\")) return "/";
  return raw;
}}

/** End the session because it expired, remembering where the user was. */
export function endSession(): void {{
  clearSession();
  if (typeof window === "undefined") return;
  if (window.location.pathname === LOGIN_ROUTE) return;
  const here = window.location.pathname + window.location.search;
  // A full navigation, not a client-side push: it guarantees no component is
  // still holding data fetched under the dead session.
  window.location.assign(`${{LOGIN_ROUTE}}?next=${{encodeURIComponent(here)}}`);
}}

/**
 * End the session because the user asked to.
 *
 * `POST /auth/logout` is the only party that can end it: it revokes the refresh
 * family server-side and clears the httpOnly cookie, and script cannot delete
 * an httpOnly cookie itself. So the request has to succeed before this can
 * claim the user is signed out.
 *
 * On failure it returns false and does NOT navigate. Clearing local state and
 * showing the sign-in page while the cookie is still live would be a lie: the
 * very next visit to any page would refresh straight off that cookie and sign
 * the user back in. Reporting the failure is the honest outcome.
 *
 * Note the endpoint is subject-wide — it ends every session for this user, on
 * every device, not just this browser.
 */
export async function signOut(): Promise<boolean> {{
  const token = readRefreshToken();

  let res: Response;
  try {{
    res = await fetch(`${{API_BASE}}/auth/logout`, {{
      method: "POST",
      credentials: "include",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify(token === null ? {{}} : {{ refresh_token: token }}),
    }});
  }} catch (cause) {{
    console.error("Sign-out could not be sent", cause);
    return false;
  }}

  if (!res.ok) {{
    console.error("Sign-out was rejected", res.status);
    return false;
  }}

  clearSession();
  if (typeof window !== "undefined") window.location.assign(LOGIN_ROUTE);
  return true;
}}
"""
    )
    return GeneratedFile(path="frontend/src/lib/session.ts", content=content, provenance=auth.fqn)


def _generate_app_shell(ir: DomainIR, auth: AuthSpec) -> GeneratedFile:
    """The client shell that gates every page except sign-in."""
    header = provenance_header("typescript", auth.fqn, "Authenticated app shell")
    content = (
        header.rstrip()
        + """

"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";

import { AppSidebar } from "@/components/AppSidebar";
import { LOGIN_ROUTE } from "@/lib/config";
import { endSession, hasAccessToken, refreshSession, subscribe } from "@/lib/session";

type Status = "checking" | "ready" | "denied";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === LOGIN_ROUTE;

  if (isLogin) {
    return <main className="min-h-screen">{children}</main>;
  }
  return <Gate>{children}</Gate>;
}

function Gate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let live = true;

    async function bootstrap() {
      // A reload drops the in-memory access token by design, so the first
      // thing every page load does is trade the refresh token for a new one.
      if (hasAccessToken() || (await refreshSession())) {
        if (live) setStatus("ready");
        return;
      }
      if (!live) return;
      setStatus("denied");
      endSession();
    }

    bootstrap();
    return () => {
      live = false;
    };
  }, []);

  // Re-render when a request elsewhere in the app ends the session, so the
  // page stops showing data the user is no longer entitled to.
  useEffect(
    () =>
      subscribe(() => {
        if (!hasAccessToken()) setStatus("denied");
      }),
    [],
  );

  if (status === "checking") {
    return (
      <div className="flex min-h-screen items-center justify-center text-gray-500">
        Checking your session...
      </div>
    );
  }

  if (status === "denied") {
    return (
      <div className="flex min-h-screen items-center justify-center text-gray-500">
        Signing you out...
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-gray-50">
      <AppSidebar />
      <main className="min-w-0 flex-1 p-8">{children}</main>
    </div>
  );
}
"""
    )
    return GeneratedFile(
        path="frontend/src/components/AppShell.tsx", content=content, provenance=auth.fqn
    )


def _generate_login_page(ir: DomainIR, auth: AuthSpec) -> GeneratedFile:
    header = provenance_header("typescript", auth.fqn, "Sign-in page")
    title = ir.domain.replace("_", " ").title()
    content = (
        header.rstrip()
        + f'''

"use client";

import {{ useEffect, useState }} from "react";

import {{ Button }} from "@/components/ui/button";
import {{ Input }} from "@/components/ui/input";
import {{ safeNext, signIn }} from "@/lib/session";

export default function LoginPage() {{
  const [identity, setIdentity] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [next, setNext] = useState("/");

  // Read from location rather than useSearchParams: the latter forces every
  // page under this route into a Suspense boundary at build time.
  useEffect(() => {{
    const raw = new URLSearchParams(window.location.search).get("next");
    setNext(safeNext(raw));
  }}, []);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {{
    event.preventDefault();
    if (pending) return;

    const trimmed = identity.trim();
    if (!trimmed || !password) {{
      setError("Enter your {auth.identity_label.lower()} and password.");
      return;
    }}

    setPending(true);
    setError(null);
    const result = await signIn(trimmed, password);
    if (result.ok) {{
      // A full navigation so every module re-initialises under the new
      // session rather than reusing state from the signed-out render.
      window.location.assign(next);
      return;
    }}
    setPending(false);
    setPassword("");
    setError(result.message);
  }}

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 p-4">
      <form
        onSubmit={{handleSubmit}}
        className="w-full max-w-sm space-y-4 rounded-lg border bg-white p-8 shadow-sm"
      >
        <div>
          <h1 className="text-xl font-bold">{title}</h1>
          <p className="text-sm text-gray-500">Sign in to continue</p>
        </div>

        {{error !== null && (
          <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
            {{error}}
          </p>
        )}}

        <div>
          <label htmlFor="identity" className="mb-1 block text-sm font-medium text-gray-700">
            {auth.identity_label}
          </label>
          <Input
            id="identity"
            name="{auth.identity_field}"
            type="{auth.identity_input_type}"
            autoComplete="username"
            required
            value={{identity}}
            onChange={{(event) => setIdentity(event.target.value)}}
          />
        </div>

        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">
            Password
          </label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={{password}}
            onChange={{(event) => setPassword(event.target.value)}}
          />
        </div>

        <Button type="submit" className="w-full" disabled={{pending}}>
          {{pending ? "Signing in..." : "Sign in"}}
        </Button>
      </form>
    </div>
  );
}}
'''
    )
    return GeneratedFile(
        path="frontend/src/app/login/page.tsx", content=content, provenance=auth.fqn
    )
