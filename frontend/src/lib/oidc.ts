// Browser sign-in with the firm's identity provider (Keycloak, full profile; M16c).
//
// OAuth 2.0 authorization code flow with PKCE (RFC 7636), for a public client
// ("fabric-ui": no secret can be kept in a browser):
// 1. startSignIn: make a random `verifier`, send its SHA-256 (`challenge`) and a random
//    `state` to Keycloak's authorize page; Keycloak signs the user in.
// 2. Keycloak sends the browser back with `?code=...&state=...`.
// 3. finishSignIn: check the state, then trade the code AND the verifier for tokens. A
//    stolen code is useless without the verifier, which never left this tab.
// The access token (a JWT, 5 minutes) is what the API checks; the refresh token renews it
// shortly before it expires. Tokens live in sessionStorage, like the lite session token.

import { clearToken, saveToken } from "./session";

export interface OidcConfig {
  issuer: string; // e.g. http://127.0.0.1:8180/realms/fabric
  clientId: string; // fabric-ui
}

interface Pending extends OidcConfig {
  verifier: string;
  state: string;
  redirectUri: string;
  returnTo: string;
}

interface Stored extends OidcConfig {
  refreshToken: string | null;
  idToken: string | null;
  expiresAt: number; // ms since the epoch
}

const PENDING_KEY = "enterprise-fabric-ai.oidc-pending";
const STORED_KEY = "enterprise-fabric-ai.oidc";
const RENEW_BEFORE_MS = 30_000;

function read<T>(key: string): T | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown | null): void {
  try {
    if (value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage blocked: the sign-in lasts for this page view only
  }
}

export function base64url(bytes: Uint8Array): string {
  let text = "";
  for (const b of bytes) text += String.fromCharCode(b);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomString(bytes = 32): string {
  return base64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

/** The PKCE S256 challenge of a verifier: base64url(SHA-256(verifier)). */
export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64url(new Uint8Array(digest));
}

const endpoint = (cfg: OidcConfig, name: "auth" | "token" | "logout") =>
  `${cfg.issuer.replace(/\/$/, "")}/protocol/openid-connect/${name}`;

/** The URL of Keycloak's sign-in page for this tab (remembers verifier and state). */
export async function startSignIn(cfg: OidcConfig, redirectUri = `${window.location.origin}/`,
  returnTo = window.location.pathname): Promise<string> {
  const pending: Pending = { ...cfg, verifier: randomString(), state: randomString(16), redirectUri, returnTo };
  write(PENDING_KEY, pending);
  const params = new URLSearchParams({
    response_type: "code", client_id: cfg.clientId, redirect_uri: redirectUri, scope: "openid",
    state: pending.state, code_challenge: await challengeFor(pending.verifier), code_challenge_method: "S256",
  });
  return `${endpoint(cfg, "auth")}?${params}`;
}

async function tokenRequest(cfg: OidcConfig, body: Record<string, string>): Promise<void> {
  const response = await fetch(endpoint(cfg, "token"), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ client_id: cfg.clientId, ...body }),
  });
  if (!response.ok) throw new Error("The identity provider refused the sign-in. Try again.");
  const t = await response.json();
  saveToken(t.access_token);
  write(STORED_KEY, { issuer: cfg.issuer, clientId: cfg.clientId, refreshToken: t.refresh_token ?? null,
    idToken: t.id_token ?? null, expiresAt: Date.now() + Number(t.expires_in ?? 60) * 1000 } satisfies Stored);
}

/** Complete a sign-in when the page is Keycloak's redirect back (?code=&state=). Returns
 * the path to show next, or null when this page is not a sign-in redirect. */
export async function finishSignIn(search = window.location.search): Promise<string | null> {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  const state = params.get("state");
  if (!code && !params.get("error")) return null;
  const pending = read<Pending>(PENDING_KEY);
  write(PENDING_KEY, null); // a code is usable once
  if (params.get("error")) throw new Error("Sign-in was cancelled or refused by the identity provider.");
  if (!pending || !state || state !== pending.state) {
    throw new Error("This sign-in link is not from this browser tab. Sign in again.");
  }
  await tokenRequest(pending, { grant_type: "authorization_code", code: code!, redirect_uri: pending.redirectUri,
    code_verifier: pending.verifier });
  return pending.returnTo || "/";
}

/** Renew the access token shortly before it expires (called before every API call). */
export async function ensureFreshToken(now = Date.now()): Promise<void> {
  const stored = read<Stored>(STORED_KEY);
  if (!stored || stored.expiresAt - now > RENEW_BEFORE_MS) return;
  if (!stored.refreshToken) return;
  try {
    await tokenRequest(stored, { grant_type: "refresh_token", refresh_token: stored.refreshToken });
  } catch {
    endOidcSession(); // the API will answer 401 and the app returns to the sign-in screen
  }
}

/** Forget the tokens; returns Keycloak's sign-out URL (to end the single sign-on too). */
export function endOidcSession(redirect = `${window.location.origin}/`): string | null {
  const stored = read<Stored>(STORED_KEY);
  write(STORED_KEY, null);
  clearToken();
  if (!stored) return null;
  const params = new URLSearchParams({ client_id: stored.clientId, post_logout_redirect_uri: redirect });
  if (stored.idToken) params.set("id_token_hint", stored.idToken);
  return `${endpoint(stored, "logout")}?${params}`;
}

export const hasOidcSession = () => read<Stored>(STORED_KEY) !== null;
