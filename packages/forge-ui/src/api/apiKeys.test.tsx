import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useApiKeys, useCreateApiKey, useRevokeApiKey } from "./apiKeys";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useApiKeys", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("unwraps the backend's {tokens: [...]} envelope (TokenListResponse) into a bare array", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          tokens: [
            {
              id: "u_1",
              label: "laptop CLI",
              roles: ["user"],
              created_at: "2026-01-01T00:00:00Z",
              expires_at: "2026-02-01T00:00:00Z",
              revoked_at: null,
            },
          ],
        }),
      }),
    );

    const { result } = renderHook(() => useApiKeys(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // The hook must resolve to the unwrapped array, not the {tokens: [...]}
    // envelope the gateway actually returns.
    expect(result.current.data).toEqual([
      {
        id: "u_1",
        label: "laptop CLI",
        roles: ["user"],
        created_at: "2026-01-01T00:00:00Z",
        expires_at: "2026-02-01T00:00:00Z",
        revoked_at: null,
      },
    ]);
  });
});

describe("useCreateApiKey", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.cookie = "forge_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("POSTs {label, roles, ttl_seconds} with the X-CSRF-Token header and resolves the raw token", async () => {
    document.cookie = "forge_csrf=create-token-abc";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({
        id: "u_2",
        token: "forge_sk_u_2_abc123",
        label: "ci key",
        roles: ["user"],
        created_at: "2026-01-01T00:00:00Z",
        expires_at: "2026-01-08T00:00:00Z",
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useCreateApiKey(), { wrapper });
    result.current.mutate({ label: "ci key", roles: ["user"], ttl_seconds: 604800 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.token).toBe("forge_sk_u_2_abc123");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/auth/tokens");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe(
      "create-token-abc",
    );
    expect(JSON.parse(init.body as string)).toEqual({
      label: "ci key",
      roles: ["user"],
      ttl_seconds: 604800,
    });
  });
});

describe("useRevokeApiKey", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("DELETEs /v1/auth/tokens/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => undefined,
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useRevokeApiKey(), { wrapper });
    result.current.mutate("u_1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/auth/tokens/u_1");
    expect(init.method).toBe("DELETE");
  });
});
