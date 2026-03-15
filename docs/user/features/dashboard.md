---
layout: page
title: "Dashboard"
description: "Monitor your Forge AI agent's health, stats, and system information from the dashboard."
tier: user
nav_order: 3
---

# Dashboard

The dashboard is the landing page of the control plane. It provides a real-time overview of your agent's health, operational stats, and system configuration.

![Dashboard]({{ site.baseurl }}/assets/images/screenshots/dashboard.png)

## Health Status

The health status badge at the top of the dashboard shows the overall state of your agent:

| Status | Meaning |
|--------|---------|
| **Healthy** | All probes pass and all subsystems are operational. Displayed with a green badge. |
| **Degraded** | One or more subsystems are reporting issues. Displayed with a yellow or red badge. Scroll down to Health Checks for details. |

The health indicator dot also appears in the **header bar** at the top of every page -- green when healthy, red when degraded -- so you always have visibility into agent status regardless of which page you are on.

## Stats cards

Three cards display key operational metrics:

| Card | Description |
|------|-------------|
| **Tools** | The total number of tools currently registered with the agent (OpenAPI, manual, and workflow tools combined). |
| **Active Sessions** | The number of active conversation sessions. Each session represents an ongoing chat thread with message history. |
| **Connected Peers** | The number of peer agents configured in the agents.peers section of your configuration. |

## Quick Actions

Three shortcut buttons give you one-click access to the most common tasks:

| Action | Destination |
|--------|-------------|
| **Edit Config** | Opens the [Config Builder]({{ site.baseurl }}/user/features/config-builder/) where you can modify your agent's configuration. |
| **Add Tool** | Opens the [Tool Workshop]({{ site.baseurl }}/user/features/tools/) with the Add Tool dialog ready. |
| **Open Chat** | Opens the [Chat]({{ site.baseurl }}/user/features/chat/) interface where you can start or resume a conversation with your agent. |

## System Information

This section displays metadata and runtime configuration read from the active forge.yaml:

| Field | Description |
|-------|-------------|
| **Name** | The agent's configured name (from `metadata.name`). |
| **Version** | The agent's version string (from `metadata.version`). |
| **Model** | The default LLM model in use (from `llm.default_model`), for example `gpt-4o`. |
| **LiteLLM Mode** | How the LLM routing layer is deployed: `embedded` (in-process), `sidecar` (separate container), or `external` (remote service). |
| **Description** | A human-readable description of the agent (from `metadata.description`). |

## Health Checks

The Health Checks section runs the gateway's Kubernetes-style health probes and reports the result of each:

### Probes

| Probe | Endpoint | What it checks |
|-------|----------|----------------|
| **Liveness** | `/health/live` | The gateway process is running and responsive. If this fails, the process is likely hung or crashed. |
| **Readiness** | `/health/ready` | The gateway has finished initialization and is ready to serve requests. Returns 503 during startup or if the agent is not fully loaded. |
| **Startup** | `/health/startup` | The gateway has completed its startup sequence. Returns 503 until the lifespan context has finished initializing. |

### Subsystem checks

Below the probes, individual subsystem checks may appear (depending on your configuration). These report on specific components like the LLM connection, tool registry status, and AgentWeave initialization. A failing subsystem check is what causes the overall status to show **Degraded**.

## Refreshing data

The dashboard includes a **Refresh** button that re-fetches all health, stats, and system data from the gateway API. The dashboard also performs periodic auto-refresh so the data stays reasonably current without manual intervention.

If you notice stale data, click Refresh or reload the browser page.
