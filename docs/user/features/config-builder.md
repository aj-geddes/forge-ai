---
layout: page
title: "Config Builder"
description: "Edit your Forge AI configuration with the visual editor, YAML editor, or diff view."
tier: user
nav_order: 4
---

# Config Builder

The Config Builder is the control plane's configuration management interface. It lets you view, edit, validate, and save your agent's `forge.yaml` configuration without leaving the browser. Changes are written to disk and trigger a hot-reload so the agent picks them up automatically.

## Overview

The Config Builder has three editing modes, accessible via tabs at the top of the page:

- **Visual** -- structured form-based editing with accordion sections
- **YAML** -- a full-featured code editor for direct YAML editing
- **Diff** -- a comparison view showing changes between your edits and the saved configuration

Two action buttons appear at the top:

| Button | Action |
|--------|--------|
| **Save** | Validates your configuration, writes it to `forge.yaml` on the server, and triggers a hot-reload. If validation fails, an error message describes the problem. |
| **Reload** | Discards your unsaved changes and re-fetches the current configuration from the server. |

## Visual Editor

![Config Builder - Visual mode]({{ site.baseurl }}/assets/images/screenshots/config-visual.png)

The Visual Editor organizes the configuration into collapsible accordion sections. Click a section header to expand or collapse it.

### Metadata

Basic information about your agent deployment.

| Field | Description |
|-------|-------------|
| **Name** | The agent instance name (e.g., `my-forge-agent`). Used in logs, A2A agent cards, and the dashboard. |
| **Version** | A version string for your deployment (e.g., `0.1.0`). |
| **Description** | A human-readable description shown on the dashboard and in the A2A agent card. |
| **Environment** | The deployment environment: `development`, `staging`, or `production`. |

### LLM Configuration

Controls how your agent connects to language model providers.

| Field | Description |
|-------|-------------|
| **Default Model** | The model identifier used for conversations (e.g., `gpt-4o`, `claude-sonnet`). Must match a model in your LiteLLM model list. |
| **Temperature** | Controls response randomness. Range: 0.0 (deterministic) to 2.0 (creative). Default: `0.7`. |
| **Max Tokens** | Maximum number of tokens in the model's response. Default: `4096`. |
| **System Prompt** | A default system prompt applied to all agents unless overridden at the agent level. |
| **LiteLLM Mode** | How LiteLLM is deployed: **embedded** (runs in the same process), **sidecar** (separate container, requires endpoint), or **external** (remote service, requires endpoint). |
| **Model List** | The list of model definitions with provider routing. Each entry maps a friendly model name to a provider-specific model identifier and API key. |
| **Fallback Models** | Ordered list of models to try if the primary model is unavailable. |
| **Timeout** | Request timeout in seconds for LLM calls. Default: `30.0`. |
| **Max Retries** | Number of retry attempts for failed LLM requests. Default: `3`. |

### Tools

Manage the tools available to your agent. See the [Tool Workshop]({{ site.baseurl }}/user/features/tools/) page for detailed tool management.

| Section | Description |
|---------|-------------|
| **OpenAPI Sources** | API specifications that are automatically parsed into tools. Each source has a name, URL or path, namespace, tag/operation filters, and authentication settings. |
| **Manual Tools** | Custom-defined tools with explicit parameters and API call configuration. |
| **Workflows** | Multi-step tool sequences that chain multiple tools together with conditional logic. |

### Security

Security and access control settings. See the [Security]({{ site.baseurl }}/user/features/security/) page for details.

| Section | Description |
|---------|-------------|
| **AgentWeave** | Identity verification, message signing, trust domain, authorization provider, and trust policy. |
| **API Keys** | Enable/disable API key authentication and manage key references. |
| **Rate Limiting** | Requests-per-minute limit applied to incoming API requests. |
| **Allowed Origins** | CORS origin allowlist for browser-based access. |

### Agents

Define agent personas and peer connections.

| Section | Description |
|---------|-------------|
| **Default Agent** | The name of the agent persona used when no specific agent is requested. |
| **Agent Definitions** | A list of named agent personas, each with its own description, system prompt, model override, tool filter, and maximum turn count. |
| **Peers** | Peer agents for A2A communication. Managed in more detail on the [Peers]({{ site.baseurl }}/user/features/peers/) page. |

## YAML Editor

![Config Builder - YAML mode]({{ site.baseurl }}/assets/images/screenshots/config-yaml.png)

The YAML Editor provides a CodeMirror-powered code editor with:

- **Syntax highlighting** for YAML
- **Line numbers** for easy reference
- **Full-text editing** of the raw `forge.yaml` content

This mode is useful when you need to make bulk changes, copy-paste configuration snippets, or edit fields that the Visual Editor does not expose.

When you click **Save**, the YAML is parsed and validated against the ForgeConfig schema before being written to disk. If validation fails, the error message will indicate which field is invalid and why.

## Diff view

The Diff view shows a comparison between your current edits and the last saved configuration. This is helpful for reviewing what has changed before clicking Save, especially after making multiple edits across different sections.

Changes are highlighted with standard diff coloring: additions in green, deletions in red.

## Tips

- **Always Reload after external changes.** If someone else edits the config file directly (or another system modifies it), click Reload to fetch the latest version before making your own edits.
- **Secrets are redacted.** Secret references (environment variable names and Kubernetes secret keys) are displayed as `***REDACTED***` in the config response. You can set new secret references in the Visual Editor, but you will not see existing secret values.
- **Validation happens before save.** The server validates the full configuration against the Pydantic schema before writing it to disk. If a required field is missing or a value has the wrong type, you will get an error message and the save will not proceed.
- **Hot-reload is automatic.** After a successful save, the gateway watches the config file and automatically reloads the agent, tool surface, MCP server, and A2A agent card. You do not need to restart the server.
