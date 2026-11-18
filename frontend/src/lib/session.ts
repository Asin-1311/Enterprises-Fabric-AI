// Where the sign-in token lives in the browser.
//
// sessionStorage: the token is forgotten when the tab or browser is closed, and each tab
// signs in separately. Storage can be unavailable (private mode, blocked site data), so
// every access is wrapped and an in-memory copy is kept for the current page view.

const KEY = "enterprise-fabric-ai.token";
let memoryToken: string | null = null;

export function currentToken(): string | null {
  try {
    return window.sessionStorage.getItem(KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function saveToken(token: string): void {
  memoryToken = token;
  try {
    window.sessionStorage.setItem(KEY, token);
  } catch {
    // Not fatal: the in-memory copy lasts until the page is reloaded.
  }
}

export function clearToken(): void {
  memoryToken = null;
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}
