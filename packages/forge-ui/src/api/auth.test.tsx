import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useAuth, useLogout } from "./auth";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useAuth", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reports isAuthenticated: false and no user while GET /v1/auth/me is 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: "missing_credentials" }),
        statusText: "Unauthorized",
      }),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.isAuthenticated).toBe(false);
    expect(result.current.user).toBeNull();
  });

  it("does NOT redirect to /auth/login when the auth check itself 401s", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/", search: "", assign },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: "missing_credentials" }),
        statusText: "Unauthorized",
      }),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(assign).not.toHaveBeenCalled();
  });

  it("exposes user, permissions and a working can() once GET /v1/auth/me succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          kind: "user",
          sub: "CgdhamdlZGRlcxIGZ2l0aHVi",
          email: "ageddes75@gmail.com",
          name: "AJ Geddes",
          groups: ["hvs-platform:admins"],
          roles: ["admin"],
          permissions: ["*"],
        }),
      }),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    expect(result.current.user?.email).toBe("ageddes75@gmail.com");
    expect(result.current.can("config:write")).toBe(true); // wildcard "*"
  });

  it("can() reflects a non-admin permission set precisely", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          kind: "user",
          sub: "user-1",
          email: "someone@example.com",
          name: "Someone",
          groups: [],
          roles: ["viewer"],
          permissions: ["config:read", "metrics:read"],
        }),
      }),
    );

    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.isAuthenticated).toBe(true));

    expect(result.current.can("config:read")).toBe(true);
    expect(result.current.can("config:write")).toBe(false);
  });
});

describe("useLogout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("POSTs /auth/logout with the X-CSRF-Token header from the forge_csrf cookie", async () => {
    document.cookie = "forge_csrf=logout-token";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => undefined,
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useLogout(), { wrapper });
    result.current.mutate();

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/auth/logout");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("logout-token");

    document.cookie = "forge_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });
});
