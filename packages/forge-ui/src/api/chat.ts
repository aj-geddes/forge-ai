// SSE consumer for POST /v1/chat/completions?stream=true
// (docs/developer/api-reference.md "Streaming responses").
//
// This module owns the raw fetch + ReadableStream parsing so ChatPage can
// stay focused on rendering. It follows the same BFF cookie contract as
// client.ts (httpOnly session cookie + CSRF double-submit header) but can't
// reuse `apiRequest` because that helper always calls `res.json()`, which
// consumes (and is incompatible with) a streaming response body.

import { CSRF_HEADER_NAME, readCookie, CSRF_COOKIE_NAME } from "./client";

export interface ToolCallRecord {
  name: string;
  arguments: Record<string, unknown>;
  result?: unknown;
}

export interface StreamChatCompletionParams {
  message: string;
  sessionId: string;
  agent?: string;
}

export interface StreamChatCompletionCallbacks {
  onChunk?: (text: string) => void;
  onToolCall?: (toolCall: ToolCallRecord) => void;
}

interface ChatStreamFrame {
  chunk?: string;
  tool_call?: ToolCallRecord;
  error?: string;
  session_id?: string;
}

const DONE_SENTINEL = "[DONE]";
const SSE_DATA_PREFIX = "data: ";
const SSE_EVENT_SEPARATOR = "\n\n";

function parseSseEvent(rawEvent: string): ChatStreamFrame | typeof DONE_SENTINEL | null {
  const dataLine = rawEvent.split("\n").find((line) => line.startsWith(SSE_DATA_PREFIX));
  if (!dataLine) return null;

  const data = dataLine.slice(SSE_DATA_PREFIX.length);
  if (data === DONE_SENTINEL) return DONE_SENTINEL;

  return JSON.parse(data) as ChatStreamFrame;
}

function dispatchFrame(
  frame: ChatStreamFrame,
  callbacks: StreamChatCompletionCallbacks,
): void {
  if (frame.tool_call) {
    callbacks.onToolCall?.(frame.tool_call);
  } else if (typeof frame.chunk === "string") {
    callbacks.onChunk?.(frame.chunk);
  } else if (frame.error) {
    throw new Error(frame.error);
  }
}

/**
 * POST a chat message with `stream: true` and consume the resulting SSE
 * response, invoking `onChunk` for each text delta and `onToolCall` for
 * each tool invocation, in the order the server emits them.
 */
export async function streamChatCompletion(
  params: StreamChatCompletionParams,
  callbacks: StreamChatCompletionCallbacks,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const csrfToken = readCookie(CSRF_COOKIE_NAME);
  if (csrfToken) {
    headers[CSRF_HEADER_NAME] = csrfToken;
  }

  const response = await fetch("/v1/chat/completions", {
    method: "POST",
    headers,
    credentials: "include",
    body: JSON.stringify({
      message: params.message,
      session_id: params.sessionId,
      agent: params.agent,
      stream: true,
    }),
  });

  if (!response.ok || !response.body) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(
      (error as { detail?: string; error?: string }).detail ||
        (error as { detail?: string; error?: string }).error ||
        "Request failed",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    let separatorIndex = buffer.indexOf(SSE_EVENT_SEPARATOR);
    while (separatorIndex !== -1) {
      const rawEvent = buffer.slice(0, separatorIndex);
      buffer = buffer.slice(separatorIndex + SSE_EVENT_SEPARATOR.length);

      const parsed = parseSseEvent(rawEvent);
      if (parsed === DONE_SENTINEL) return;
      if (parsed) dispatchFrame(parsed, callbacks);

      separatorIndex = buffer.indexOf(SSE_EVENT_SEPARATOR);
    }
  }

  if (buffer.trim()) {
    const parsed = parseSseEvent(buffer);
    if (parsed && parsed !== DONE_SENTINEL) dispatchFrame(parsed, callbacks);
  }
}
