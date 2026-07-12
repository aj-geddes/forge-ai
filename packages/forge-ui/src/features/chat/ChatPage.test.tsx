import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ChatPage } from "./ChatPage";
import { useChatStore } from "@/stores/chatStore";

function renderChatPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatPage />
    </QueryClientProvider>,
  );
}

describe("ChatPage", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
    useChatStore.setState({
      sessions: [],
      activeSessionId: null,
      isLoading: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the agent's reply from the backend's `message` field, not `response`", async () => {
    // The gateway's ConversationResponse model (forge_gateway/models.py) returns
    // {message, session_id, tools_used, model} -- never a `response` field.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/v1/admin/sessions")) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => [],
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            message: "Hello from the agent",
            session_id: "session-1",
            tools_used: [],
            model: "gpt-4o",
          }),
        });
      }),
    );

    const user = userEvent.setup();
    renderChatPage();

    await user.click(screen.getByRole("button", { name: /new session/i }));

    const textbox = screen.getByPlaceholderText(/ask the agent anything/i);
    await user.type(textbox, "Hi there{Enter}");

    await waitFor(() => {
      expect(screen.getByText("Hello from the agent")).toBeInTheDocument();
    });
  });
});
