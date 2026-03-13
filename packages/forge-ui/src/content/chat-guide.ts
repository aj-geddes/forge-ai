import type { GuideSection } from "./index";

export const chatGuide: GuideSection = {
  id: "chat-guide",
  title: "Chat Guide",
  overview:
    "Learn how to use the conversational interface to interact with your Forge AI agent, manage sessions, and understand tool calls.",
  concepts: [
    {
      title: "Chat Sessions",
      description:
        "Each conversation is a session with its own history. You can have multiple sessions and switch between them freely.",
      icon: "MessageSquare",
    },
    {
      title: "Tool Calls",
      description:
        "When the agent uses a tool, you will see the tool call and its result inline in the chat. This gives you full visibility into agent actions.",
      icon: "Wrench",
    },
    {
      title: "System Prompts",
      description:
        "The agent's behavior is guided by its system prompt, configured in forge.yaml. Different agents can have different system prompts.",
      icon: "FileText",
    },
    {
      title: "Streaming Responses",
      description:
        "Responses stream in real-time as the LLM generates them. You can see the agent thinking and forming its response progressively.",
      icon: "Radio",
    },
  ],
  steps: [
    {
      title: "Start a new session",
      content:
        "Navigate to the Chat page and click 'New Session' to start a fresh conversation. Your previous sessions are saved in the sidebar.",
    },
    {
      title: "Send a message",
      content:
        "Type your message in the input field and press Enter or click Send. The agent will process your message and respond.",
    },
    {
      title: "Observe tool usage",
      content:
        "When the agent decides to use a tool, you will see a collapsible tool call block showing the tool name, parameters, and result.",
    },
    {
      title: "Review history",
      content:
        "Scroll up to see previous messages in the session. You can also switch to other sessions from the session list.",
    },
  ],
  examples: [
    {
      title: "Chat API Request",
      language: "bash",
      code: `curl -X POST http://localhost:8000/api/chat \\
  -H "Content-Type: application/json" \\
  -d '{
    "message": "What pets are available?",
    "session_id": "my-session"
  }'`,
    },
    {
      title: "Streaming Chat",
      language: "bash",
      code: `curl -X POST http://localhost:8000/api/chat/stream \\
  -H "Content-Type: application/json" \\
  -H "Accept: text/event-stream" \\
  -d '{
    "message": "Tell me about the store inventory",
    "session_id": "my-session"
  }'`,
    },
  ],
  tryIt: { label: "Open Chat", path: "/chat" },
  related: ["tools-guide", "getting-started"],
};
