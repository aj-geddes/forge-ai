---
layout: page
title: "Getting Started"
description: "Log in to the Forge AI control plane, explore the dashboard, and learn the key concepts."
tier: user
nav_order: 2
---

# Getting Started

This page walks you through your first login, gives you a tour of the dashboard, and introduces the key concepts you will encounter throughout the control plane.

## What Forge AI does

Forge AI is a config-driven AI agent platform that turns a single YAML file into a fully operational agent with tool access, security controls, and multi-protocol interfaces. The control plane UI is the browser-based management layer -- it lets you monitor health, edit configuration, manage tools, chat with your agent, and coordinate with peer agents, all without touching the command line.

## Accessing the control plane

The control plane is served directly by the Forge AI gateway. Your administrator will provide you with:

1. **The URL** -- typically `http://<host>:<port>/` (the gateway serves the UI at the root path)
2. **An API key** -- used to authenticate all requests to the admin API

The default port is `8000` unless your deployment uses a custom configuration.

## Logging in

When you open the control plane URL in your browser, you will see the login screen.

![Login screen]({{ site.baseurl }}/assets/images/screenshots/login.png)

To log in:

1. Enter your API key in the input field. This is the value configured under `security.api_keys` in forge.yaml.
2. Click **Sign In** (or press Enter).
3. If the key is valid, you are taken to the dashboard.

Your API key is stored in your browser's `localStorage` so you stay authenticated across page reloads. If the server returns a `401 Unauthorized` response at any point (for example, if the key is rotated), the UI will automatically log you out and return you to the login screen.

**Tip:** The API key is sent as a `Bearer` token in the `Authorization` header or as an `X-API-Key` header with every request.

## The dashboard

After logging in, you land on the dashboard -- the home page of the control plane.

![Dashboard]({{ site.baseurl }}/assets/images/screenshots/dashboard.png)

The dashboard gives you an at-a-glance view of your agent's status. It is divided into several sections described in detail on the [Dashboard]({{ site.baseurl }}/user/features/dashboard/) page:

- **Health Status** -- a badge showing **Healthy** (green) or **Degraded** (yellow/red)
- **Stats cards** -- Tools count, Active Sessions count, Connected Peers count
- **Quick Actions** -- shortcuts to Edit Config, Add Tool, and Open Chat
- **System Information** -- agent name, version, default model, LiteLLM mode, description
- **Health Checks** -- individual probe results (Liveness, Readiness, Startup) and subsystem checks

## First steps

Here is a recommended path for getting oriented:

### 1. Check health

Look at the Health Status badge at the top of the dashboard. If it shows **Healthy**, all subsystems are operational. If it shows **Degraded**, scroll down to the Health Checks section to see which probe or subsystem is failing. See [Troubleshooting]({{ site.baseurl }}/user/troubleshooting/) for help.

### 2. Explore the configuration

Click **Edit Config** in the Quick Actions area (or navigate to **Config** in the sidebar). The [Config Builder]({{ site.baseurl }}/user/features/config-builder/) opens with three tabs:

- **Visual** -- accordion sections for each configuration area
- **YAML** -- a full code editor with syntax highlighting
- **Diff** -- side-by-side comparison of your changes against the saved config

### 3. Try the chat

Click **Open Chat** in Quick Actions (or navigate to **Chat** in the sidebar). Create a new session and send a message to your agent. You will see the assistant's response stream in, and any tool calls will appear with expandable details. See [Chat]({{ site.baseurl }}/user/features/chat/) for more.

## Key concepts

These terms appear throughout the control plane:

| Concept | Description |
|---------|-------------|
| **Agent** | A named AI persona with its own system prompt, model, tool access, and turn limits. Forge AI can host multiple agent definitions, with one designated as the default. |
| **Tool** | A capability the agent can invoke during a conversation. Tools come in three types: **OpenAPI** (auto-imported from an API spec), **Manual** (custom-defined with parameters and an API endpoint), and **Workflow** (a multi-step sequence of other tools). |
| **Peer** | Another Forge AI instance (or compatible A2A agent) that your agent can communicate with. Each peer has a trust level (high, medium, or low) and a list of capabilities. |
| **Session** | A conversation thread between a user and the agent. Each session maintains its own message history and can use any of the configured tools. |
| **LiteLLM** | The LLM routing layer that connects your agent to language model providers (OpenAI, Anthropic, etc.). It runs in one of three modes: embedded, sidecar, or external. |
| **AgentWeave** | The security framework that provides identity verification, message signing, audit logging, and authorization for agent-to-agent communication. |
| **Hot-reload** | Forge AI watches the config file for changes and automatically reloads when it detects a modification. The UI Save button writes to the file, which triggers the reload. |
