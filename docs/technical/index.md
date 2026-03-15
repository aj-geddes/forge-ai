---
layout: page
title: Technical Reference
description: Architecture, data models, infrastructure, security, performance, and observability documentation for Forge AI.
parent: Technical
nav_order: 1
---

# Technical Reference

This tier provides detailed technical documentation for engineers building, deploying, and operating Forge AI. All content is derived from the source code and verified against the actual implementation.

## Documents

| Document | Description |
|----------|-------------|
| [System Design](system-design.md) | Architecture overview, component diagram, request flow, technology stack, and design patterns |
| [Data Model](data-model.md) | Pydantic configuration schema, model relationships, YAML format, and secret resolution |
| [Infrastructure](infrastructure.md) | Docker multi-stage build, Helm chart, Kubernetes resources, deployment profiles, and CI/CD |
| [Security](security.md) | Authentication layers, SecurityGate pipeline, SSRF protection, secret management, CORS, and rate limiting |
| [Performance](performance.md) | Async-first design, config hot-reload, tool registry hot-swap, LiteLLM modes, and caching strategies |
| [Monitoring](monitoring.md) | Health probes, Prometheus metrics, request logging, Kubernetes probes, and ServiceMonitor |

## Architecture at a Glance

Forge AI is a config-driven AI agent system built as a **uv monorepo workspace** with four Python packages arranged in a strict dependency chain:

```
forge-config --> forge-security --> forge-agent --> forge-gateway
```

The system exposes REST, MCP, and A2A interfaces through a FastAPI gateway, serves a React SPA control plane UI, and supports hot-reload of both configuration and tool surfaces without restarts.

## Key Source Locations

| Package | Source Path | Purpose |
|---------|-----------|---------|
| `forge-config` | `packages/forge-config/src/forge_config/` | Pydantic config schema, YAML loader, hot-reload watcher, secret resolution |
| `forge-security` | `packages/forge-security/src/forge_security/` | Identity, signing, audit, rate limiting, trust policy, SecurityGate middleware |
| `forge-agent` | `packages/forge-agent/src/forge_agent/` | PydanticAI agent core, tool builders (OpenAPI, manual, workflow), tool registry |
| `forge-gateway` | `packages/forge-gateway/src/forge_gateway/` | FastAPI app, routes (health, admin, chat, MCP, A2A, metrics), auth, SPA serving |
| Helm chart | `deploy/helm/forge/` | Kubernetes deployment with small/medium/large profiles |
| Dockerfile | `Dockerfile` | Multi-stage build targeting less than 200MB |
| Config reference | `forge.yaml.example` | Canonical configuration file example |
