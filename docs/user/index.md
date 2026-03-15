---
layout: page
title: "User Guide"
description: "End-user documentation for the Forge AI control plane — manage your agent instance through the React UI."
tier: user
nav_order: 1
---

# Forge AI User Guide

Welcome to the Forge AI User Guide. This documentation covers the React-based control plane UI that lets you manage, configure, and interact with your Forge AI agent instance.

## What is Forge AI?

Forge AI is a config-driven AI agent platform. You define your agent's capabilities, tools, security posture, and peer connections in a single YAML configuration file, and Forge AI turns that into a running agent with REST, MCP, and A2A (agent-to-agent) interfaces. The control plane UI gives you a graphical interface to manage all of this without editing YAML by hand.

## What does the control plane do?

The control plane is a browser-based dashboard that lets you:

- **Monitor** your agent's health, active sessions, connected tools, and peer agents
- **Configure** every aspect of your agent through a visual editor, raw YAML editor, or diff view
- **Build and manage tools** by importing OpenAPI specs, defining manual tools, or composing multi-step workflows
- **Chat** with your agent directly, with full visibility into tool calls and streaming responses
- **Manage peers** by adding, pinging, and configuring trust levels for other Forge AI instances
- **Review security** settings including AgentWeave identity verification, rate limiting, CORS, and API keys

## Who is this guide for?

This guide is written for **operators** and **developers** who interact with a running Forge AI instance through the browser UI. You do not need to know Python or understand the internal architecture -- this guide focuses on what you see and what you can do in the control plane.

## Guide contents

| Page | Description |
|------|-------------|
| [Getting Started]({{ site.baseurl }}/user/getting-started/) | First login, dashboard orientation, key concepts |
| [Dashboard]({{ site.baseurl }}/user/features/dashboard/) | Health status, stats, quick actions, system info |
| [Config Builder]({{ site.baseurl }}/user/features/config-builder/) | Visual editor, YAML editor, diff view |
| [Tool Workshop]({{ site.baseurl }}/user/features/tools/) | Adding and managing OpenAPI, manual, and workflow tools |
| [Chat]({{ site.baseurl }}/user/features/chat/) | Chatting with your agent, sessions, tool call details |
| [Peers]({{ site.baseurl }}/user/features/peers/) | A2A peer connections, trust levels, ping |
| [Security]({{ site.baseurl }}/user/features/security/) | AgentWeave, rate limiting, CORS, API keys |
| [Configuration Reference]({{ site.baseurl }}/user/configuration/) | Complete forge.yaml option reference |
| [FAQ]({{ site.baseurl }}/user/faq/) | Frequently asked questions |
| [Troubleshooting]({{ site.baseurl }}/user/troubleshooting/) | Common errors and how to fix them |
