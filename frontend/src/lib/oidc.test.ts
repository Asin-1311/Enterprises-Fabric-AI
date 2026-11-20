import { challengeFor, endOidcSession, ensureFreshToken, finishSignIn, hasOidcSession, startSignIn } from "./oidc";
import { currentToken } from "./session";

const CFG = { issuer: "http://kc.test/realms/fabric", clientId: "fabric-ui" };

afterEach(() => {
  window.sessionStorage.clear();
  vi.unstubAllGlobals();
});

function tokenEndpoint(response: Record<string, unknown>, status = 200) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => Response.json(response, { status }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

test("the S256 challenge matches the RFC 7636 example", async () => {
  expect(await challengeFor("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"))
    .toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
});

test("sign-in goes to Keycloak with a PKCE challenge and a random state", async () => {
  const url = new URL(await startSignIn(CFG, "http://app.test/", "/admin"));
  expect(url.origin + url.pathname).toBe("http://kc.test/realms/fabric/protocol/openid-connect/auth");
  const p = url.searchParams;
  expect(p.get("response_type")).toBe("code");
  expect(p.get("client_id")).toBe("fabric-ui");
  expect(p.get("redirect_uri")).toBe("http://app.test/");
  expect(p.get("code_challenge_method")).toBe("S256");
  expect(p.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(p.get("state")).toBeTruthy();
  expect(p.toString()).not.toContain("verifier"); // the verifier never leaves the tab
});

test("the redirect back trades the code and the verifier for tokens, once", async () => {
  const url = new URL(await startSignIn(CFG, "http://app.test/", "/admin"));
  const fetchMock = tokenEndpoint({ access_token: "jwt-1", refresh_token: "r-1", id_token: "id-1", expires_in: 300 });
  const next = await finishSignIn(`?code=c-123&state=${url.searchParams.get("state")}`);
  expect(next).toBe("/admin");
  expect(currentToken()).toBe("jwt-1");
  const [endpoint, init] = fetchMock.mock.calls[0];
  expect(endpoint).toBe("http://kc.test/realms/fabric/protocol/openid-connect/token");
  const body = new URLSearchParams(String(init!.body));
  expect(body.get("grant_type")).toBe("authorization_code");
  expect(body.get("code")).toBe("c-123");
  expect(await challengeFor(body.get("code_verifier")!)).toBe(url.searchParams.get("code_challenge"));
  // The same code again is refused: the pending sign-in was used up.
  await expect(finishSignIn(`?code=c-123&state=${url.searchParams.get("state")}`)).rejects.toThrow(/not from this browser tab/);
});

test("a state that does not match this tab is refused; a normal page is not a redirect", async () => {
  await startSignIn(CFG, "http://app.test/");
  const fetchMock = tokenEndpoint({ access_token: "never" });
  await expect(finishSignIn("?code=c&state=forged")).rejects.toThrow(/not from this browser tab/);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(await finishSignIn("")).toBeNull();
  await expect(finishSignIn("?error=access_denied")).rejects.toThrow(/cancelled or refused/);
});

test("the access token is renewed shortly before it expires, and sign-out ends the session", async () => {
  const url = new URL(await startSignIn(CFG, "http://app.test/"));
  tokenEndpoint({ access_token: "jwt-1", refresh_token: "r-1", id_token: "id-1", expires_in: 300 });
  await finishSignIn(`?code=c&state=${url.searchParams.get("state")}`);
  const refresh = tokenEndpoint({ access_token: "jwt-2", refresh_token: "r-2", expires_in: 300 });
  await ensureFreshToken(Date.now()); // 5 minutes left: nothing to do
  expect(refresh).not.toHaveBeenCalled();
  await ensureFreshToken(Date.now() + 290_000); // 10 s left: renew
  expect(new URLSearchParams(String(refresh.mock.calls[0][1]!.body)).get("refresh_token")).toBe("r-1");
  expect(currentToken()).toBe("jwt-2");

  const logout = new URL(endOidcSession("http://app.test/")!);
  expect(logout.pathname).toBe("/realms/fabric/protocol/openid-connect/logout");
  expect(logout.searchParams.get("post_logout_redirect_uri")).toBe("http://app.test/");
  expect(currentToken()).toBeNull();
  expect(hasOidcSession()).toBe(false);
});
