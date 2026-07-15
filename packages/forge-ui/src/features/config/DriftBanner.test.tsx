import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DriftBanner } from "./DriftBanner";
import { useConfigEnvelope, useConfigPromotionDiff } from "@/api/hooks";

vi.mock("@/api/hooks", () => ({
  useConfigEnvelope: vi.fn(),
  useConfigPromotionDiff: vi.fn(),
}));

// The component only reads `.data` off each hook's result -- these mocks
// intentionally return a minimal partial shape, not a full React Query
// result, so cast through `unknown` rather than widening the mock's return
// type with `any`.
type EnvelopeResult = ReturnType<typeof useConfigEnvelope>;
type PromotionDiffResult = ReturnType<typeof useConfigPromotionDiff>;

function mockEnvelope(data: Record<string, unknown> | undefined) {
  vi.mocked(useConfigEnvelope).mockReturnValue({ data } as unknown as EnvelopeResult);
}

function mockPromotionDiff(data: Record<string, unknown> | null | undefined) {
  vi.mocked(useConfigPromotionDiff).mockReturnValue({ data } as unknown as PromotionDiffResult);
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DriftBanner", () => {
  it("renders nothing when drift_from_git is false", () => {
    mockEnvelope({ rev: 4, drift_from_git: false });
    mockPromotionDiff(null);

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders nothing while useConfigEnvelope() data is still undefined", () => {
    mockEnvelope(undefined);
    mockPromotionDiff(null);

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("renders the banner when drift_from_git is true and not yet dismissed for this rev", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 0 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("displays correct plural text when changed_count is 3", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 3 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    const text = screen.getByText((_, node) => node?.textContent === "3 runtime changes are live on this instance but not in Git — not permanent until you promote it (and lost only if the data volume is destroyed).");
    expect(text).toBeInTheDocument();
  });

  it("displays singular text when changed_count is 1", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 1 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    const text = screen.getByText((_, node) => node?.textContent === "1 runtime change are live on this instance but not in Git — not permanent until you promote it (and lost only if the data volume is destroyed).");
    expect(text).toBeInTheDocument();
  });

  it("displays fallback text when changed_count is undefined", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({});

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.getByText("Runtime changes are live on this instance but not in Git — not permanent until you promote it (and lost only if the data volume is destroyed).")).toBeInTheDocument();
  });

  it("does not claim changes are lost on redeploy -- the overlay survives on the durable PVC", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.queryByText(/lost on the next redeploy/i)).toBeNull();
    expect(screen.queryByText(/redeploy/i)).toBeNull();
  });

  it("states the true risk: not permanent until promoted, lost only if the volume is destroyed", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(
      screen.getByText(/not permanent until you promote it \(and lost only if the data volume is destroyed\)/i),
    ).toBeInTheDocument();
  });

  it("contains a link to /config?tab=promote with text 'Review & promote'", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    const link = screen.getByRole("link", { name: /review & promote/i });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/config?tab=promote");
  });

  it("hides the banner on dismiss and writes rev to sessionStorage", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.getByRole("status")).toBeInTheDocument();

    const dismissButton = screen.getByRole("button", { name: /dismiss/i });
    fireEvent.click(dismissButton);

    expect(screen.queryByRole("status")).toBeNull();
    expect(window.sessionStorage.getItem("forge:drift-dismissed-rev")).toBe("4");
  });

  it("reappears when a new rev is received after being dismissed for previous rev", () => {
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    const { rerender } = render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    // Dismiss for rev 4
    const dismissButton = screen.getByRole("button", { name: /dismiss/i });
    fireEvent.click(dismissButton);
    expect(screen.queryByRole("status")).toBeNull();

    // Update to new rev 5
    mockEnvelope({ rev: 5, drift_from_git: true });
    mockPromotionDiff({ changed_count: 1 });

    rerender(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("does not render when already dismissed for current rev", () => {
    window.sessionStorage.setItem("forge:drift-dismissed-rev", "4");
    mockEnvelope({ rev: 4, drift_from_git: true });
    mockPromotionDiff({ changed_count: 2 });

    render(
      <MemoryRouter>
        <DriftBanner />
      </MemoryRouter>
    );

    expect(screen.queryByRole("status")).toBeNull();
  });
});
