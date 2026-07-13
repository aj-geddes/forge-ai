// BFF-mode API client (ADR-0001). The gateway holds the session -- this
// module never stores, reads, or attaches a bearer token. It:
//   1. always sends the httpOnly session cookie (`credentials: "include"`);
//   2. echoes the readable `forge_csrf` cookie as `X-CSRF-Token` on every
//      state-changing request (the double-submit CSRF defence, ADR section
//      4.2 -- GET/HEAD are exempt);
//   3. on 401 ("session gone/expired"), does a full browser navigation to
//      `/auth/login` -- a cookie-authenticated SPA cannot recover from a
//      dead session by retrying a fetch, and 401 is never proof that *this*
//      request's data was bad, only that there is no session;
//   4. on 403 ("authenticated but not permitted"), never redirects -- that
//      would produce the classic login loop for a user who is signed in but
//      lacks a permission. Callers inspect `ApiError.status` and render a
//      permission-denied state instead.

const API_BASE = "";
const CSRF_COOKIE_NAME = "forge_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD"]);

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const match = document.cookie
    .split("; ")
    .find((row) => row.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

/** Full browser navigation to the gateway's OIDC entry point -- never a fetch/XHR. */
function redirectToLogin(): void {
  if (typeof window === "undefined") return;
  const next = `${window.location.pathname}${window.location.search}`;
  window.location.assign(`/auth/login?next=${encodeURIComponent(next)}`);
}

export interface ApiRequestOptions extends RequestInit {
  /**
   * Suppress the automatic redirect-to-login on a 401. Used only by the
   * `GET /v1/auth/me` check itself, where a 401 means "not signed in yet"
   * -- an expected state the caller renders (the login page), not a session
   * that expired mid-use.
   */
  skipAuthRedirect?: boolean;
}

export async function apiRequest<T>(
  path: string,
  options?: ApiRequestOptions,
): Promise<T> {
  const { skipAuthRedirect, ...init } = options ?? {};
  const method = (init.method ?? "GET").toString().toUpperCase();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };

  if (!SAFE_METHODS.has(method)) {
    const csrfToken = readCookie(CSRF_COOKIE_NAME);
    if (csrfToken) {
      headers[CSRF_HEADER_NAME] = csrfToken;
    }
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (!res.ok) {
    if (res.status === 401 && !skipAuthRedirect) {
      redirectToLogin();
    }
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(
      res.status,
      error.detail || error.error || "Request failed",
    );
  }

  if (res.status === 204) {
    return undefined as T;
  }

  return res.json().catch(() => undefined as T) as Promise<T>;
}

export const api = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    apiRequest<T>(path, options),

  post: <T>(path: string, body?: unknown, options?: ApiRequestOptions) =>
    apiRequest<T>(path, {
      ...options,
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }),

  put: <T>(path: string, body?: unknown, options?: ApiRequestOptions) =>
    apiRequest<T>(path, {
      ...options,
      method: "PUT",
      body: body ? JSON.stringify(body) : undefined,
    }),

  delete: <T>(path: string, options?: ApiRequestOptions) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
};
