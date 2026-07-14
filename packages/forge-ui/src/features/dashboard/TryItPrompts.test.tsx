import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import type * as ReactRouterDom from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TryItPrompts } from "./TryItPrompts";
import { useChatStore } from "@/stores/chatStore";

const navigateMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof ReactRouterDom>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => navigateMock,
  };
});

function stubToolsFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url.includes("/v1/admin/tools")) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => [
            { name: "get_weather", description: "Gets current weather for a city" },
            { name: "get_crypto_price", description: "Gets the current price of a cryptocurrency" },
            { name: "define_word", description: "Looks up a dictionary definition" },
            { name: "github_search", description: "Searches GitHub repositories" },
            { name: "exchange_rate", description: "Converts between currencies" },
          ],
        });
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => [] });
    }),
  );
}

function renderTryIt() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TryItPrompts />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TryItPrompts", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    navigateMock.mockClear();
    useChatStore.setState({ pendingPrompt: null });
  });

  it("renders between 4 and 6 example prompt chips tailored to the real tools", async () => {
    stubToolsFetch();
    renderTryIt();

    await waitFor(() => {
      expect(screen.getAllByRole("button").length).toBeGreaterThanOrEqual(4);
    });
    expect(screen.getAllByRole("button").length).toBeLessThanOrEqual(6);
  });

  it("queues the prompt on chatStore and navigates to /chat when a chip is clicked", async () => {
    stubToolsFetch();
    renderTryIt();

    const user = userEvent.setup();
    await waitFor(() => {
      expect(screen.getAllByRole("button").length).toBeGreaterThan(0);
    });

    const firstChip = screen.getAllByRole("button")[0]!;
    const promptText = firstChip.textContent ?? "";
    await user.click(firstChip);

    expect(useChatStore.getState().pendingPrompt).toBeTruthy();
    expect(promptText).toContain(useChatStore.getState().pendingPrompt);
    expect(navigateMock).toHaveBeenCalledWith("/chat");
  });
});
