import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useToolPreview, usePingPeer } from "./hooks";

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useToolPreview", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("POSTs {source} and resolves the backend's {tools, count} shape (AdminToolPreviewRequest/Response)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        tools: [{ name: "get_weather", description: "Gets weather", source: "openapi" }],
        count: 1,
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useToolPreview(), { wrapper });

    result.current.mutate({
      name: "openapi-source",
      url: "https://api.example.com/openapi.json",
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual({
      tools: [{ name: "get_weather", description: "Gets weather", source: "openapi" }],
      count: 1,
    });

    const [, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(options.body as string) as Record<string, unknown>;
    expect(body).toEqual({
      source: { name: "openapi-source", url: "https://api.example.com/openapi.json" },
    });
  });
});

describe("usePingPeer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("resolves the backend's literal peer ping shape ({name, status: 'reachable'|'unreachable'})", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ name: "peer-a", status: "reachable", http_status: 200 }),
      }),
    );

    const { result } = renderHook(() => usePingPeer(), { wrapper });

    result.current.mutate("peer-a");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual({
      name: "peer-a",
      status: "reachable",
      http_status: 200,
    });
  });
});
