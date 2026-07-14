import { useCallback, useState } from "react";
import { MessageCircle, Send, Loader2, Wrench } from "lucide-react";
import { streamChatCompletion } from "@/api/chat";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { Eyebrow, HelpText } from "./shared";

const EPHEMERAL_SESSION_PREFIX = "dashboard-mini";

function generateEphemeralSessionId(): string {
  return `${EPHEMERAL_SESSION_PREFIX}-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

interface MiniMessage {
  role: "user" | "assistant";
  content: string;
  toolsUsed: string[];
}

function replaceLastMessage(
  messages: MiniMessage[],
  updater: (message: MiniMessage) => MiniMessage,
): MiniMessage[] {
  if (messages.length === 0) return messages;
  return messages.map((message, index) =>
    index === messages.length - 1 ? updater(message) : message,
  );
}

/**
 * A compact, self-contained embedded chat widget on the Dashboard -- no
 * sidebar, no persisted sessions (a fresh ephemeral session id per mount).
 * Reuses `streamChatCompletion` (the same CSRF/cookie contract as the full
 * Chat page) rather than reimplementing the fetch/SSE plumbing.
 */
export function MiniChat() {
  const [sessionId] = useState(generateEphemeralSessionId);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<MiniMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isLoading) return;

    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, toolsUsed: [] },
      { role: "assistant", content: "", toolsUsed: [] },
    ]);
    setIsLoading(true);

    try {
      await streamChatCompletion(
        { message: text, sessionId },
        {
          onChunk: (delta) => {
            setMessages((prev) =>
              replaceLastMessage(prev, (m) => ({ ...m, content: m.content + delta })),
            );
          },
          onToolCall: (toolCall) => {
            setMessages((prev) =>
              replaceLastMessage(prev, (m) => ({
                ...m,
                toolsUsed: [...m.toolsUsed, toolCall.name],
              })),
            );
          },
        },
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : "Something went wrong.";
      setMessages((prev) =>
        replaceLastMessage(prev, (m) => ({ ...m, content: `${m.content}Error: ${message}` })),
      );
    } finally {
      setIsLoading(false);
    }
  }, [input, isLoading, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  };

  return (
    <section className="space-y-2">
      <Eyebrow icon={MessageCircle}>Quick chat</Eyebrow>
      <HelpText>Ask the agent something right here -- this is a throwaway session, not saved to Chat history.</HelpText>

      <div className="rounded-lg border bg-card">
        {messages.length > 0 && (
          <div className="max-h-64 space-y-3 overflow-y-auto p-4">
            {messages.map((message, index) => (
              <div
                key={index}
                className={cn(
                  "max-w-[85%] rounded-lg px-3 py-2 text-sm",
                  message.role === "user"
                    ? "ml-auto bg-primary text-primary-foreground"
                    : "bg-muted text-foreground",
                )}
              >
                <p className="whitespace-pre-wrap">
                  {message.content || (isLoading && index === messages.length - 1 ? "…" : "")}
                </p>
                {message.toolsUsed.length > 0 && (
                  <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
                    <Wrench className="h-3 w-3" />
                    Used: {message.toolsUsed.join(", ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2 border-t p-3">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask the agent anything..."
            rows={1}
            className="min-h-[40px] max-h-[100px] resize-none"
            disabled={isLoading}
          />
          <Button
            onClick={() => void handleSend()}
            disabled={!input.trim() || isLoading}
            size="icon"
            aria-label="Send"
          >
            {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </section>
  );
}
