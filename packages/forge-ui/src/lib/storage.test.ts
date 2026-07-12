import { afterEach, describe, expect, it, vi } from "vitest";
import { safeStorage } from "./storage";

describe("safeStorage", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("returns null for a key that was never set", () => {
    expect(safeStorage.getItem("missing-key")).toBeNull();
  });

  it("returns a previously stored value", () => {
    safeStorage.setItem("test-key", "test-value");
    expect(safeStorage.getItem("test-key")).toBe("test-value");
  });

  it("persists a value written with setItem", () => {
    safeStorage.setItem("persist-key", "persist-value");
    expect(window.localStorage.getItem("persist-key")).toBe("persist-value");
  });

  it("removes a value written with removeItem", () => {
    safeStorage.setItem("remove-key", "remove-value");
    safeStorage.removeItem("remove-key");
    expect(window.localStorage.getItem("remove-key")).toBeNull();
  });

  it("returns null instead of throwing when localStorage.getItem throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError: access denied");
    });
    expect(() => safeStorage.getItem("any-key")).not.toThrow();
    expect(safeStorage.getItem("any-key")).toBeNull();
  });

  it("does not throw when localStorage.setItem throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => safeStorage.setItem("any-key", "any-value")).not.toThrow();
  });

  it("does not throw when localStorage.removeItem throws", () => {
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("SecurityError: access denied");
    });
    expect(() => safeStorage.removeItem("any-key")).not.toThrow();
  });
});
