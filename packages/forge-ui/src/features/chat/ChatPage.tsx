import { useCallback, useEffect, useRef, useState } from "react";
import {
  MessageSquare,
  Plus,
  Send,
  Loader2,
  User,
  Bot,
  ChevronDown,
  Wrench,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chatStore";
import type { Message } from "@/stores/chatStore";
import { api } from "@/api/client";
import { useSessions, useDeleteSession } from "@/api/hooks";

interface ChatCompletionResponse {
  response: string;
  session_id: string;
  tools_used?: string[];
}

function ToolCallDetails({ tools }: { tools: string[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-2 rounded-md border bg-muted/50">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <Wrench className="h-3 w-3" />
        <span>{tools.length} tool{tools.length !== 1 ? "s" : ""} used</span>
        <ChevronDown
          className={cn(
            "ml-auto h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="border-t px-3 py-2">
          <div className="flex flex-wrap gap-1.5">
            {tools.map((tool) => (
              <Badge key={tool} variant="secondary" className="text-xs">
                {tool}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  const isUser = message.role === "user";

  return (
    <div
      className={cn(
        "flex gap-3",
        isUser ? "flex-row-reverse" : "flex-row",
      )}
    >
      <div
        className={cn(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full",
          isUser
            ? "bg-primary text-primary-foreground"
            : "bg-muted text-muted-foreground",
        )}
      >
        {isUser ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
      </div>
      <div
        className={cn(
          "max-w-[75%] space-y-1",
          isUser ? "items-end" : "items-start",
        )}
      >
        <div
          className={cn(
            "rounded-lg px-4 py-2.5 text-sm",
            isUser
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-foreground",
          )}
        >
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
        {message.toolsUsed && message.toolsUsed.length > 0 && (
          <ToolCallDetails tools={message.toolsUsed} />
        )}
        <p className="px-1 text-xs text-muted-foreground">
          {new Date(message.timestamp).toLocaleTimeString()}
        </p>
      </div>
    </div>
  );
}

function SessionSidebar() {
  const { sessions, activeSessionId, createSession, setActiveSession } =
    useChatStore();
  const { data: serverSessions } = useSessions();
  const deleteSession = useDeleteSession();

  return (
    <div className="flex h-full w-64 flex-col border-r bg-muted/30">
      <div className="p-3">
        <Button
          onClick={() => createSession()}
          className="w-full"
          size="sm"
        >
          <Plus className="h-4 w-4" />
          New Session
        </Button>
      </div>
      <Separator />
      <ScrollArea className="flex-1">
        <div className="space-y-1 p-2">
          {sessions.map((session) => (
            <button
              key={session.id}
              type="button"
              onClick={() => setActiveSession(session.id)}
              className={cn(
                "group flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
                activeSessionId === session.id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
              )}
            >
              <MessageSquare className="h-3.5 w-3.5 shrink-0" />
              <span className="min-w-0 flex-1 truncate">
                {session.id.slice(0, 20)}...
              </span>
              <span className="shrink-0 text-xs opacity-60">
                {session.messages.length}
              </span>
            </button>
          ))}
          {serverSessions?.map((ss) => {
            const isLocal = sessions.some((s) => s.id === ss.session_id);
            if (isLocal) return null;
            return (
              <div
                key={ss.session_id}
                className="group flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-muted-foreground"
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{ss.session_id.slice(0, 20)}...</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteSession.mutate(ss.session_id);
                  }}
                  className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                </button>
              </div>
            );
          })}
          {sessions.length === 0 && !serverSessions?.length && (
            <p className="px-3 py-6 text-center text-xs text-muted-foreground">
              No sessions yet. Click &quot;New Session&quot; to start.
            </p>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function ChatArea() {
  const { sessions, activeSessionId, isLoading, addMessage, setLoading } =
    useChatStore();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const activeSession = sessions.find((s) => s.id === activeSessionId);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [activeSession?.messages.length, scrollToBottom]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || !activeSessionId || isLoading) return;

    const userMessage: Message = {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: "user",
      content: input.trim(),
      timestamp: Date.now(),
    };

    addMessage(activeSessionId, userMessage);
    setInput("");
    setLoading(true);

    try {
      const response = await api.post<ChatCompletionResponse>(
        "/v1/chat/completions",
        {
          message: userMessage.content,
          session_id: activeSessionId,
          stream: false,
        },
      );

      const assistantMessage: Message = {
        id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: "assistant",
        content: response.response,
        toolsUsed: response.tools_used,
        timestamp: Date.now(),
      };

      addMessage(activeSessionId, assistantMessage);
    } catch (err) {
      const errorContent =
        err instanceof Error ? err.message : "An error occurred";

      const errorMessage: Message = {
        id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
        role: "assistant",
        content: `Error: ${errorContent}`,
        timestamp: Date.now(),
      };

      addMessage(activeSessionId, errorMessage);
    } finally {
      setLoading(false);
    }
  }, [input, activeSessionId, isLoading, addMessage, setLoading]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void handleSend();
      }
    },
    [handleSend],
  );

  if (!activeSession) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <div className="text-center">
          <MessageSquare className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium text-muted-foreground">
            No session selected
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Create a new session or select an existing one to start chatting.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="flex items-center gap-3 border-b px-4 py-3">
        <MessageSquare className="h-5 w-5 text-muted-foreground" />
        <div>
          <h2 className="text-sm font-medium">Session</h2>
          <p className="text-xs text-muted-foreground">
            {activeSession.id}
          </p>
        </div>
        <Badge variant="secondary" className="ml-auto">
          {activeSession.messages.length} messages
        </Badge>
      </div>

      {/* Messages */}
      <ScrollArea className="flex-1 p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          {activeSession.messages.length === 0 && (
            <div className="flex min-h-[200px] items-center justify-center">
              <p className="text-sm text-muted-foreground">
                Send a message to start the conversation.
              </p>
            </div>
          )}
          {activeSession.messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} />
          ))}
          {isLoading && (
            <div className="flex gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                <Bot className="h-4 w-4" />
              </div>
              <div className="flex items-center gap-2 rounded-lg bg-muted px-4 py-2.5 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Thinking...
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </ScrollArea>

      {/* Input */}
      <div className="border-t p-4">
        <div className="mx-auto flex max-w-3xl gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
            className="min-h-[44px] max-h-[120px] resize-none"
            disabled={isLoading}
            rows={1}
          />
          <Button
            onClick={() => void handleSend()}
            disabled={!input.trim() || isLoading}
            size="icon"
            className="h-[44px] w-[44px] shrink-0"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ChatPage() {
  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden rounded-lg border bg-card">
      <SessionSidebar />
      <ChatArea />
    </div>
  );
}
