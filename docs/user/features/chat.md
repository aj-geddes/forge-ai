---
layout: page
title: "Chat"
description: "Chat with your Forge AI agent, manage sessions, and inspect tool calls."
tier: user
nav_order: 6
---

# Chat

The Chat interface lets you have conversations with your Forge AI agent directly in the control plane. You can create multiple sessions, see the agent's tool usage in real time, and manage your conversation history.

![Chat interface]({{ site.baseurl }}/assets/images/screenshots/chat.png)

## Layout

The Chat page is split into two areas:

- **Session sidebar** (left) -- lists your conversation sessions with a **New Session** button at the top
- **Chat area** (right) -- the message thread and input field for the active session

## Creating a session

Click the **New Session** button in the sidebar to start a fresh conversation. A new session is created with a unique ID and appears in the session list. The session uses the default agent persona unless your configuration specifies otherwise.

## Sending messages

1. Type your message in the input field at the bottom of the chat area.
2. Press **Enter** to send.
3. Use **Shift+Enter** to insert a newline without sending (for multi-line messages).

## Assistant responses

When the agent responds, you will see:

- **Message bubbles** -- user messages appear on the right, assistant messages on the left, with distinct visual styling for each.
- **Streaming responses** -- the assistant's reply streams in token-by-token so you see it as it is generated rather than waiting for the complete response.
- **Tool call details** -- when the agent invokes a tool during its response, a tool call block appears in the conversation. Each tool call shows:
  - The **tool name** (e.g., `get_weather`, `find_pets`)
  - The **arguments** passed to the tool
  - The **result** returned by the tool
  - An expandable/collapsible view so you can inspect the details or collapse them to keep the conversation readable

Tool calls are displayed inline within the assistant's response, in the order they were executed.

## Session management

### Switching sessions

Click any session in the sidebar to switch to it. The chat area updates to show that session's message history.

### Viewing server sessions

The session sidebar shows sessions stored on the server. If sessions were created through the API (outside the UI), they also appear here.

### Deleting sessions

You can delete a session to remove it from the server. This permanently removes the conversation history for that session.

## Tips

- **Context carries over.** Each session maintains its full message history, so the agent remembers earlier parts of the conversation within the same session.
- **Tool results are preserved.** If the agent called a tool earlier in the session, the result is part of the conversation context and informs future responses.
- **Long conversations.** Sessions are bounded by the agent's `max_turns` setting (default: 10 turns). If you hit the limit, start a new session.
- **Model behavior.** The agent uses the default model configured under `llm.default_model` unless the active agent persona specifies a model override.
