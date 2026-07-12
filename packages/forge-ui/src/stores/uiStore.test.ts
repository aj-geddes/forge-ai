import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("uiStore", () => {
  beforeEach(() => {
    vi.resetModules();
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("does not throw on import when localStorage.getItem throws, and falls back to false", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Simulated localStorage error");
    });

    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it("defaults sidebarCollapsed to false when localStorage is empty", async () => {
    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    expect(useUIStore.getState().darkMode).toBe(false);
    expect(useUIStore.getState().guideOpen).toBe(false);
  });

  it("reads sidebarCollapsed from localStorage when set before import", async () => {
    window.localStorage.setItem("forge-sidebar-collapsed", "true");

    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
  });

  it("toggleSidebar flips sidebarCollapsed and persists to localStorage", async () => {
    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    expect(window.localStorage.getItem("forge-sidebar-collapsed")).toBeNull();

    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    expect(window.localStorage.getItem("forge-sidebar-collapsed")).toBe("true");

    useUIStore.getState().toggleSidebar();
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
    expect(window.localStorage.getItem("forge-sidebar-collapsed")).toBe("false");
  });

  it("toggleDarkMode flips darkMode and persists to localStorage", async () => {
    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().darkMode).toBe(false);
    expect(window.localStorage.getItem("forge-dark-mode")).toBeNull();

    useUIStore.getState().toggleDarkMode();
    expect(useUIStore.getState().darkMode).toBe(true);
    expect(window.localStorage.getItem("forge-dark-mode")).toBe("true");

    useUIStore.getState().toggleDarkMode();
    expect(useUIStore.getState().darkMode).toBe(false);
    expect(window.localStorage.getItem("forge-dark-mode")).toBe("false");
  });

  it("toggleGuide flips guideOpen without touching localStorage", async () => {
    const { useUIStore } = await import("./uiStore");

    expect(useUIStore.getState().guideOpen).toBe(false);

    useUIStore.getState().toggleGuide();
    expect(useUIStore.getState().guideOpen).toBe(true);

    useUIStore.getState().toggleGuide();
    expect(useUIStore.getState().guideOpen).toBe(false);

    expect(window.localStorage.getItem("forge-guide-open")).toBeNull();
  });

  it("toggleSidebar does not throw when localStorage.setItem throws", async () => {
    const { useUIStore } = await import("./uiStore");

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("Simulated setItem error");
    });

    expect(() => useUIStore.getState().toggleSidebar()).not.toThrow();
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
  });
});
