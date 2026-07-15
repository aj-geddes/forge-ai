import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useToolPreview, usePingPeer, useCreatePeer, useActivity, useApprovals, useApproveApproval, useRejectApproval } from "./hooks";

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


describe("useCreatePeer", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // POST /v1/admin/peers (forge_gateway.routes.admin.create_peer) resolves
  // the bare created-peer shape (AdminPeerResponse) -- it does NOT carry a
  // {persisted, durable, ...} honesty envelope. Honesty is enforced at the
  // transport layer instead: the route only reaches this response after a
  // genuine successful overlay write; a rejected write always throws
  // (409/405/507) before it. A resolved mutate() here is therefore always a
  // real success -- see the doc comment on useCreatePeer in hooks.ts.
  function bareCreatedPeer(overrides?: Partial<Record<string, unknown>>) {
    return {
      name: "data-forge",
      endpoint: "https://data-forge.example.com",
      trust_level: "high",
      capabilities: ["data_query"],
      spiffe_id: "spiffe://forge.local/peer/data-forge",
      status: "unknown",
      ...overrides,
    };
  }

  it("POSTs the peer fields to /v1/admin/peers and resolves the bare created peer (no honesty envelope)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => bareCreatedPeer(),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useCreatePeer(), { wrapper });

    result.current.mutate({
      name: "data-forge",
      endpoint: "https://data-forge.example.com",
      trust_level: "high",
      capabilities: ["data_query"],
      spiffe_id: "spiffe://forge.local/peer/data-forge",
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(bareCreatedPeer());

    const [url, options] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/peers");
    expect(options.method).toBe("POST");
    const body = JSON.parse(options.body as string) as Record<string, unknown>;
    expect(body).toEqual({
      name: "data-forge",
      endpoint: "https://data-forge.example.com",
      trust_level: "high",
      capabilities: ["data_query"],
      spiffe_id: "spiffe://forge.local/peer/data-forge",
    });
  });

  it("a rejected write (e.g. 409 name already exists) surfaces as a thrown ApiError, never a resolved 'success'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 409,
        json: async () => ({ detail: "Peer 'data-forge' already exists" }),
      }),
    );

    const { result } = renderHook(() => useCreatePeer(), { wrapper });

    result.current.mutate({
      name: "data-forge",
      endpoint: "https://data-forge.example.com",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});

describe("useActivity", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("resolves the backend's {activity: ActivityEntry[]} shape to a bare array, polling /v1/admin/activity", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        activity: [
          {
            tool: "get_weather",
            arguments: { city: "SF" },
            ok: true,
            timestamp: "2026-01-01T00:00:00Z",
            session_id: "s1",
            interface: "chat",
          },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useActivity(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual([
      {
        tool: "get_weather",
        arguments: { city: "SF" },
        ok: true,
        timestamp: "2026-01-01T00:00:00Z",
        session_id: "s1",
        interface: "chat",
      },
    ]);

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/v1/admin/activity?limit=20");
  });

  it("surfaces a 403 (non-admin caller) as isError without retrying, instead of crashing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: "Forbidden" }),
      }),
    );

    const { result } = renderHook(() => useActivity(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});

describe("useApprovals", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("resolves the backend's bare ApprovalRequest[] array from /v1/admin/approvals", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [
        {
          id: "appr_1",
          tool_name: "social_publish",
          arguments: { content: "hello", channel: "twitter" },
          argument_hash: "h1",
          requested_by: "assistant",
          run_id: "run_1",
          draft_summary: null,
          created_at: "2026-07-12T00:00:00Z",
          status: "pending",
        },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApprovals(), { wrapper });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual([
      {
        id: "appr_1",
        tool_name: "social_publish",
        arguments: { content: "hello", channel: "twitter" },
        argument_hash: "h1",
        requested_by: "assistant",
        run_id: "run_1",
        draft_summary: null,
        created_at: "2026-07-12T00:00:00Z",
        status: "pending",
      },
    ]);

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/v1/admin/approvals");
  });

  it("surfaces a 403 (caller lacking config:read) as isError without retrying", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ detail: "Forbidden" }),
      }),
    );

    const { result } = renderHook(() => useApprovals(), { wrapper });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});

describe("useApproveApproval", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("POSTs the approve endpoint for the given id and resolves the decision", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "appr_1", status: "approved", result: { published: true } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useApproveApproval(), { wrapper });

    result.current.mutate("appr_1");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual({ id: "appr_1", status: "approved", result: { published: true } });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/approvals/appr_1/approve");
    expect(init.method).toBe("POST");
  });
});

describe("useRejectApproval", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("POSTs the reject endpoint for the given id and resolves the decision", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "appr_2", status: "rejected" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useRejectApproval(), { wrapper });

    result.current.mutate("appr_2");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual({ id: "appr_2", status: "rejected" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/approvals/appr_2/reject");
    expect(init.method).toBe("POST");
  });
});
