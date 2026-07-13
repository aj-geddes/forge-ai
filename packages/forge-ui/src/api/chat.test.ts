import { afterEach, describe, expect, it, vi } from "vitest";
import { streamChatCompletion } from "./chat";

function sseResponse(rawEvents: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const evt of rawEvents) {
        controller.enqueue(encoder.encode(`data: ${evt}\n\n`));
      }
      controller.close();
    },
  });
  return {
    ok: true,
    status: 200,
    body,
    json: async () => ({}),
  } as unknown as Response;
}

describe("streamChatCompletion", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    document.cookie = "forge_csrf=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
  });

  it("invokes onChunk for each text delta frame, in order", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        JSON.stringify({ chunk: "Hello", session_id: "s1" }),
        JSON.stringify({ chunk: " world", session_id: "s1" }),
        "[DONE]",
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onChunk = vi.fn();
    await streamChatCompletion(
      { message: "Hi", sessionId: "s1" },
      { onChunk },
    );

    expect(onChunk.mock.calls.map((c) => c[0])).toEqual(["Hello", " world"]);
  });

  it("invokes onToolCall for tool_call frames, distinct from chunk frames", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        JSON.stringify({
          tool_call: { name: "get_weather", arguments: { city: "SF" }, result: "sunny" },
          session_id: "s1",
        }),
        JSON.stringify({ chunk: "It's sunny.", session_id: "s1" }),
        "[DONE]",
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onChunk = vi.fn();
    const onToolCall = vi.fn();
    await streamChatCompletion(
      { message: "weather?", sessionId: "s1" },
      { onChunk, onToolCall },
    );

    expect(onToolCall).toHaveBeenCalledWith({
      name: "get_weather",
      arguments: { city: "SF" },
      result: "sunny",
    });
    expect(onChunk).toHaveBeenCalledWith("It's sunny.");
  });

  it("stops processing at the [DONE] sentinel", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([JSON.stringify({ chunk: "a", session_id: "s1" }), "[DONE]"]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const onChunk = vi.fn();
    await streamChatCompletion({ message: "Hi", sessionId: "s1" }, { onChunk });

    expect(onChunk).toHaveBeenCalledTimes(1);
  });

  it("posts stream:true along with the message and session_id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(["[DONE]"]));
    vi.stubGlobal("fetch", fetchMock);

    await streamChatCompletion({ message: "Hi there", sessionId: "sess-9" }, {});

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/v1/chat/completions");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toMatchObject({
      message: "Hi there",
      session_id: "sess-9",
      stream: true,
    });
  });

  it("sends credentials: include and the CSRF header from the forge_csrf cookie", async () => {
    document.cookie = "forge_csrf=tok-123";
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(["[DONE]"]));
    vi.stubGlobal("fetch", fetchMock);

    await streamChatCompletion({ message: "Hi", sessionId: "s1" }, {});

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.credentials).toBe("include");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-CSRF-Token"]).toBe("tok-123");
  });

  it("throws when the response is not ok", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      streamChatCompletion({ message: "Hi", sessionId: "s1" }, {}),
    ).rejects.toThrow("boom");
  });
});
