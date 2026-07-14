import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { formatRelativeTime } from "./time";

describe("formatRelativeTime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:10:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "just now" for a timestamp 5 seconds before system time', () => {
    expect(formatRelativeTime("2026-01-01T00:09:55Z")).toBe("just now");
  });

  it('returns "30s ago" for a timestamp 30 seconds before system time', () => {
    expect(formatRelativeTime("2026-01-01T00:09:30Z")).toBe("30s ago");
  });

  it('returns "5m ago" for a timestamp 5 minutes before system time', () => {
    expect(formatRelativeTime("2026-01-01T00:05:00Z")).toBe("5m ago");
  });

  it('returns "3h ago" for a timestamp 3 hours before system time', () => {
    expect(formatRelativeTime("2025-12-31T21:10:00Z")).toBe("3h ago");
  });

  it('returns "2d ago" for a timestamp 2 days before system time', () => {
    expect(formatRelativeTime("2025-12-30T00:10:00Z")).toBe("2d ago");
  });

  it("returns an invalid date string unchanged", () => {
    expect(formatRelativeTime("not-a-date")).toBe("not-a-date");
  });
});
