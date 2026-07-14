import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MiniChat } from "./MiniChat";
import { streamChatCompletion } from "@/api/chat";

vi.mock("@/api/chat", () => ({
  streamChatCompletion: vi.fn(),
}));

describe("MiniChat", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("calls streamChatCompletion with the typed message and an ephemeral session id on send", async () => {
    vi.mocked(streamChatCompletion).mockImplementation(async (_params, callbacks) => {
      callbacks.onChunk?.("Hi there!");
    });

    render(<MiniChat />);
    const user = userEvent.setup();

    const textbox = screen.getByRole("textbox");
    await user.type(textbox, "Hello agent");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(streamChatCompletion).toHaveBeenCalledTimes(1);
    });

    const [params] = vi.mocked(streamChatCompletion).mock.calls[0]!;
    expect(params.message).toBe("Hello agent");
    expect(params.sessionId).toBeTruthy();

    await waitFor(() => {
      expect(screen.getByText("Hi there!")).toBeInTheDocument();
    });
  });

  it("shows a tools-used indicator when the streamed reply includes tool calls", async () => {
    vi.mocked(streamChatCompletion).mockImplementation(async (_params, callbacks) => {
      callbacks.onToolCall?.({ name: "get_weather", arguments: { city: "SF" }, result: "sunny" });
      callbacks.onChunk?.("It's sunny in SF.");
    });

    render(<MiniChat />);
    const user = userEvent.setup();

    await user.type(screen.getByRole("textbox"), "What's the weather?");
    await user.click(screen.getByRole("button", { name: /send/i }));

    await waitFor(() => {
      expect(screen.getByText(/get_weather/)).toBeInTheDocument();
    });
  });
});
