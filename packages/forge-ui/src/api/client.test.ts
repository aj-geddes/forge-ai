import { afterEach, describe, expect, it, vi } from "vitest";
import { api, apiRequest, ApiError } from "./client";

function stubLocation() {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      pathname: "/config",
      search: "?tab=yaml",
      assign,
    },
  });
  return assign;
}

describe("apiRequest -- BFF cookie contract", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.cookie = "forge_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("always sends credentials: include so the httpOnly session cookie rides along", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/v1/admin/config");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("include");
  });

  it("attaches X-CSRF-Token from the readable forge_csrf cookie on POST", async () => {
    document.cookie = "forge_csrf=abc123";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.post("/v1/admin/config", { foo: "bar" });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("abc123");
  });

  it("attaches X-CSRF-Token on PUT and DELETE too", async () => {
    document.cookie = "forge_csrf=xyz789";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.put("/v1/admin/config", { foo: "bar" });
    await api.delete("/v1/admin/sessions/1");

    for (const call of fetchMock.mock.calls) {
      const [, init] = call as [string, RequestInit];
      const headers = init.headers as Record<string, string>;
      expect(headers["X-CSRF-Token"]).toBe("xyz789");
    }
  });

  it("does NOT attach X-CSRF-Token on GET (safe method, exempt)", async () => {
    document.cookie = "forge_csrf=abc123";
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/v1/admin/config");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("never attaches an Authorization header -- the session lives only in the cookie", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/v1/admin/config");
    await api.post("/v1/admin/config", { foo: "bar" });

    for (const call of fetchMock.mock.calls) {
      const [, init] = call as [string, RequestInit];
      const headers = init.headers as Record<string, string>;
      expect(headers.Authorization).toBeUndefined();
    }
  });

  it("on 401, performs a full browser navigation to /auth/login with next preserved", async () => {
    const assign = stubLocation();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: "missing_credentials" }),
        statusText: "Unauthorized",
      }),
    );

    await expect(api.get("/v1/admin/config")).rejects.toThrow(ApiError);

    expect(assign).toHaveBeenCalledWith(
      "/auth/login?next=" + encodeURIComponent("/config?tab=yaml"),
    );
  });

  it("on 403, does NOT navigate anywhere -- the caller renders a permission-denied state", async () => {
    const assign = stubLocation();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ error: "forbidden" }),
        statusText: "Forbidden",
      }),
    );

    const error = await api.get("/v1/admin/config").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    expect(assign).not.toHaveBeenCalled();
  });

  it("skips the redirect on 401 when skipAuthRedirect is set (the auth/me check itself)", async () => {
    const assign = stubLocation();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: "missing_credentials" }),
        statusText: "Unauthorized",
      }),
    );

    await expect(
      apiRequest("/v1/auth/me", { skipAuthRedirect: true }),
    ).rejects.toThrow(ApiError);

    expect(assign).not.toHaveBeenCalled();
  });

  it("never writes anything to localStorage or sessionStorage as part of a request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      }),
    );
    const localSpy = vi.spyOn(Storage.prototype, "setItem");

    await api.get("/v1/admin/config");
    await api.post("/v1/admin/config", { foo: "bar" });

    expect(localSpy).not.toHaveBeenCalled();
  });
});
