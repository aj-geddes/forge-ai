import { describe, it, expect, vi, afterEach } from "vitest";
import { listApprovals, approveApproval, rejectApproval } from "./approvals";

describe("listApprovals", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("GETs /v1/admin/approvals and resolves the backend's bare array", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [
        {
          id: "appr_1",
          tool_name: "social_publish",
          arguments: { content: "hello world", channel: "twitter" },
          argument_hash: "abc123",
          requested_by: "assistant",
          run_id: "run_1",
          draft_summary: null,
          created_at: "2026-07-12T00:00:00Z",
          status: "pending",
        },
      ],
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listApprovals();

    expect(result).toEqual([
      {
        id: "appr_1",
        tool_name: "social_publish",
        arguments: { content: "hello world", channel: "twitter" },
        argument_hash: "abc123",
        requested_by: "assistant",
        run_id: "run_1",
        draft_summary: null,
        created_at: "2026-07-12T00:00:00Z",
        status: "pending",
      },
    ]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/approvals");
    expect((init.method ?? "GET").toString().toUpperCase()).toBe("GET");
  });

  it("degrades an unexpected non-array response to an empty queue instead of throwing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) }),
    );

    await expect(listApprovals()).resolves.toEqual([]);
  });
});

describe("approveApproval", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.cookie = "forge_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("POSTs /v1/admin/approvals/{id}/approve with the CSRF header and resolves the decision", async () => {
    document.cookie = "forge_csrf=approve-token-abc";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "appr_1", status: "approved", result: { published: true } }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveApproval("appr_1");

    expect(result).toEqual({ id: "appr_1", status: "approved", result: { published: true } });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/approvals/appr_1/approve");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-CSRF-Token"]).toBe("approve-token-abc");
  });
});

describe("rejectApproval", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("POSTs /v1/admin/approvals/{id}/reject and resolves the decision", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "appr_2", status: "rejected" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await rejectApproval("appr_2");

    expect(result).toEqual({ id: "appr_2", status: "rejected" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/admin/approvals/appr_2/reject");
    expect(init.method).toBe("POST");
  });
});
