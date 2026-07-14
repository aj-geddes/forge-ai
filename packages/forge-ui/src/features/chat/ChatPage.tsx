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
  Info,
  Sparkles,
  Brain,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/stores/chatStore";
import type { Message } from "@/stores/chatStore";
import { streamChatCompletion } from "@/api/chat";
import type { ToolCallRecord } from "@/api/chat";
import { useSessions, useDeleteSession, useConfig, useTools } from "@/api/hooks";
import { resolveDefaultAgentName } from "@/lib/agents";
import { AgentSelector } from "@/components/agent/AgentSelector";

function ToolCallDetails({ toolCalls }: { toolCalls: ToolCallRecord[] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-2 rounded-md border bg-muted/50">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <Wrench className="h-3 w-3" />
        <span>
          {toolCalls.length} tool{toolCalls.length !== 1 ? "s" : ""} used
          {toolCalls.length > 0 && `: ${toolCalls.map((t) => t.name).join(", ")}`}
        </span>
        <ChevronDown
          className={cn(
            "ml-auto h-3 w-3 transition-transform duration-200",
            expanded && "rotate-180",
          )}
        />
      </button>
      {expanded && (
        <div className="space-y-2 border-t px-3 py-2">
          {toolCalls.map((call, index) => (
            <div key={`${call.name}-${index}`} className="space-y-1 rounded-md bg-background p-2">
              <Badge variant="secondary" className="text-xs">
                {call.name}
              </Badge>
              <div>
                <p className="text-[11px] font-medium text-muted-foreground">Arguments</p>
                <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-[11px]">
                  {JSON.stringify(call.arguments, null, 2)}
                </pre>
              </div>
              <div>
                <p className="text-[11px] font-medium text-muted-foreground">Result</p>
                <pre className="overflow-x-auto rounded bg-muted px-2 py-1 text-[11px]">
                  {JSON.stringify(call.result, null, 2)}
                </pre>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function HelpText({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground mt-1 leading-relaxed">
      <Info className="h-3 w-3 mt-0.5 shrink-0 opacity-60" />
      <span>{children}</span>
    </p>
  );
}

function MessageBubble({
  message,
  isLoading,
}: {
  message: Message;
  isLoading: boolean;
}) {
  const isUser = message.role === "user";
  // A streamed assistant reply that finished (loading ended) with no chunks
  // ever arriving would otherwise render as a confusing blank bubble.
  const isEmptyFinishedReply = !isUser && message.content === "" && !isLoading;

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
          <p
            className={cn(
              "whitespace-pre-wrap",
              isEmptyFinishedReply && "italic text-muted-foreground",
            )}
          >
            {isEmptyFinishedReply ? "(no response)" : message.content}
          </p>
        </div>
        {message.toolCalls && message.toolCalls.length > 0 && (
          <ToolCallDetails toolCalls={message.toolCalls} />
        )}
        <p className="px-1 text-xs text-muted-foreground">
          {new Date(message.timestamp).toLocaleTimeString()}
        </p>
      </div>
    </div>
  );
}

function SessionSidebar() {
  const { sessions, activeSessionId, createSession, setActiveSession, resumeSession } =
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
                className="group flex w-full items-center gap-1 rounded-md text-left text-sm text-muted-foreground"
              >
                <button
                  type="button"
                  onClick={() => resumeSession(ss.session_id)}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                  <span className="min-w-0 flex-1 truncate">{ss.session_id.slice(0, 20)}...</span>
                </button>
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
            <div className="px-3 py-6 text-center">
              <Sparkles className="mx-auto h-8 w-8 text-muted-foreground/40" />
              <p className="mt-2 text-xs font-medium text-muted-foreground">
                No sessions yet
              </p>
              <p className="mt-1 text-xs text-muted-foreground/80">
                Each session is an independent conversation with the agent.
                Click &quot;New Session&quot; above to get started.
              </p>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function ChatArea() {
  const {
    sessions,
    activeSessionId,
    isLoading,
    pendingPrompt,
    pendingAgent,
    createSession,
    addMessage,
    appendMessageContent,
    addToolCallToMessage,
    setLoading,
    setPendingPrompt,
    setPendingAgent,
    setSessionAgent,
  } = useChatStore();
  const [input, setInput] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { data: config } = useConfig();
  const { data: tools } = useTools();

  const activeSession = sessions.find((s) => s.id === activeSessionId);
  const agentRoster = config?.agents?.agents ?? [];
  const defaultAgentName = config ? resolveDefaultAgentName(config) : undefined;
  const selectedAgent = activeSession?.agent ?? defaultAgentName ?? "";

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [activeSession?.messages.length, scrollToBottom]);

  // A "Try it" chip on the Dashboard queues a prompt via chatStore; on
  // mount here we adopt it into a (possibly brand-new) session and drop it
  // into the input for the user to review/send, then clear the queue so it
  // never re-fires on a later visit. Not auto-sent -- the user still
  // confirms by pressing Send/Enter.
  useEffect(() => {
    if (pendingPrompt !== null) {
      if (!activeSessionId) {
        createSession();
      }
      setInput(pendingPrompt);
      setPendingPrompt(null);
    }
  }, [pendingPrompt, activeSessionId, createSession, setPendingPrompt]);

  // The Dashboard's compact agent strip queues an agent via chatStore; on
  // mount here we adopt it into a (possibly brand-new) session as that
  // session's persona. Mirrors the pendingPrompt effect above, but the
  // agent can only be applied once a concrete session id exists, so the
  // queue is cleared one render later than the session-creation trigger.
  useEffect(() => {
    if (pendingAgent === null) return;
    if (!activeSessionId) {
      createSession();
      return;
    }
    setSessionAgent(activeSessionId, pendingAgent);
    setPendingAgent(null);
  }, [pendingAgent, activeSessionId, createSession, setSessionAgent, setPendingAgent]);

  const handleSend = useCallback(async () => {
    if (!input.trim() || !activeSessionId || isLoading) return;

    const sessionId = activeSessionId;
    const userMessage: Message = {
      id: `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      role: "user",
      content: input.trim(),
      timestamp: Date.now(),
    };

    addMessage(sessionId, userMessage);
    setInput("");
    setLoading(true);

    const assistantMessageId = `msg-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
    addMessage(sessionId, {
      id: assistantMessageId,
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    });

    try {
      await streamChatCompletion(
        { message: userMessage.content, sessionId, agent: activeSession?.agent },
        {
          onChunk: (delta) => appendMessageContent(sessionId, assistantMessageId, delta),
          onToolCall: (toolCall) =>
            addToolCallToMessage(sessionId, assistantMessageId, toolCall),
        },
      );
    } catch (err) {
      const errorContent =
        err instanceof Error ? err.message : "An error occurred";
      appendMessageContent(sessionId, assistantMessageId, `Error: ${errorContent}`);
    } finally {
      setLoading(false);
    }
  }, [
    input,
    activeSessionId,
    isLoading,
    activeSession?.agent,
    addMessage,
    appendMessageContent,
    addToolCallToMessage,
    setLoading,
  ]);

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
        <div className="max-w-md text-center">
          <MessageSquare className="mx-auto h-12 w-12 text-muted-foreground/50" />
          <h3 className="mt-4 text-lg font-medium text-muted-foreground">
            No session selected
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Create a new session or select an existing one to start chatting.
          </p>
          <div className="mt-6 space-y-3 rounded-lg border bg-muted/30 p-4 text-left">
            <div className="flex items-start gap-2.5">
              <Brain className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <p className="text-sm font-medium">Sessions have memory</p>
                <p className="text-xs text-muted-foreground">
                  Each session is a conversation where the agent remembers context across all
                  your messages, so you can build on previous questions.
                </p>
              </div>
            </div>
            <div className="flex items-start gap-2.5">
              <Zap className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <p className="text-sm font-medium">Get started</p>
                <p className="text-xs text-muted-foreground">
                  Click &quot;New Session&quot; in the sidebar to create a conversation, then
                  type your first message. The agent will respond using its configured tools.
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3">
        <div className="flex items-center gap-3">
          <MessageSquare className="h-5 w-5 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-medium">Session</h2>
            <p className="truncate text-xs text-muted-foreground">
              {activeSession.id}
            </p>
          </div>
          <Badge variant="secondary">
            {activeSession.messages.length} messages
          </Badge>
        </div>
        <div className="mt-3 max-w-sm">
          <AgentSelector
            id="chat-agent-selector"
            agents={agentRoster}
            value={selectedAgent}
            onChange={(name) => setSessionAgent(activeSession.id, name)}
            tools={tools}
          />
        </div>
      </div>

      {/* Messages */}
      <ScrollArea className="flex-1 p-4">
        <div className="mx-auto max-w-3xl space-y-4">
          {activeSession.messages.length === 0 && (
            <div className="flex min-h-[200px] items-center justify-center">
              <div className="max-w-sm text-center">
                <Sparkles className="mx-auto h-8 w-8 text-muted-foreground/40" />
                <p className="mt-3 text-sm font-medium text-muted-foreground">
                  Ready to chat
                </p>
                <p className="mt-1 text-xs text-muted-foreground/80">
                  Send a message to start the conversation. The agent can use its configured
                  tools to search, analyze, generate content, and more. Responses will show
                  which tools were used.
                </p>
              </div>
            </div>
          )}
          {activeSession.messages.map((msg) => (
            <MessageBubble key={msg.id} message={msg} isLoading={isLoading} />
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
        <div className="mx-auto max-w-3xl">
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask the agent anything — it has access to your configured tools and remembers this session's context..."
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
          <HelpText>
            Press Enter to send, Shift+Enter for a new line. Tool calls made by the agent
            will be shown as expandable details in responses.
          </HelpText>
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
