---
layout: page
title: "FAQ"
description: "Frequently asked questions about using the Forge AI control plane."
tier: user
nav_order: 10
---

# Frequently Asked Questions

## How do I get an API key?

API keys are configured in the `security.api_keys` section of `forge.yaml`. Each key is a **secret reference** pointing to an environment variable or Kubernetes secret -- the actual key value lives outside the config file.

To set up API key authentication:

1. Set an environment variable on the server (e.g., `export FORGE_API_KEY=my-secret-key-value`).
2. Add the reference to your config:
   ```yaml
   security:
     api_keys:
       enabled: true
       keys:
         - source: env
           name: FORGE_API_KEY
   ```
3. Restart the gateway (or wait for hot-reload to pick up the change).
4. Use the value from the environment variable (e.g., `my-secret-key-value`) to log in to the control plane.

Your administrator manages the key values. If you need access, ask them for the key.

---

## What does "Degraded" health status mean?

The **Degraded** status means one or more subsystem health checks are failing while the core gateway remains operational. Common causes include:

- The LLM provider is unreachable (check your API keys and network)
- The AgentWeave SPIRE agent socket is not available
- The OPA authorization server is down
- A config validation issue occurred during hot-reload

Check the **Health Checks** section on the [Dashboard]({{ site.baseurl }}/user/features/dashboard/) to see which specific probe or subsystem is reporting a failure.

---

## Can I use models other than GPT-4o?

Yes. Forge AI uses LiteLLM for LLM routing, which supports 100+ providers including OpenAI, Anthropic, Google, Cohere, Mistral, Ollama, and more.

To use a different model:

1. Add the model to the `llm.litellm.model_list` with the appropriate provider prefix and API key:
   ```yaml
   llm:
     litellm:
       model_list:
         - model_name: claude-sonnet
           litellm_params:
             model: anthropic/claude-sonnet-4-20250514
             api_key: ${ANTHROPIC_API_KEY}
   ```
2. Set `llm.default_model` to the new model name, or assign it to a specific agent via the `model` field in the agent definition.

Refer to the [LiteLLM documentation](https://docs.litellm.ai/) for the full list of supported providers and model name formats.

---

## How does hot-reload work?

Forge AI watches the `forge.yaml` file for changes using a file system watcher (powered by the `watchdog` library). When a change is detected:

1. The watcher debounces rapid changes (waits 1 second after the last modification).
2. The new config is loaded and validated against the Pydantic schema.
3. If validation passes, the gateway updates:
   - Admin state and API routes
   - API key authentication
   - SecurityGate (AgentWeave)
   - Tool surface (rebuilds all tools from the new config)
   - MCP server (rebuilt with updated tools)
   - A2A agent card (updated with new metadata and capabilities)
4. If validation fails, the error is logged and the existing config remains active.

Hot-reload happens automatically when you click **Save** in the Config Builder, because the save writes to the config file on disk, which the watcher detects.

**Important:** Hot-reload does not require a server restart. The gateway continues serving requests throughout the reload process.

---

## What are the three LiteLLM modes?

LiteLLM can be deployed in three modes, configured via `llm.litellm.mode`:

| Mode | Description | When to use |
|------|-------------|-------------|
| **embedded** | LiteLLM runs inside the Forge AI gateway process. No external proxy needed. | Development, single-instance deployments, simplest setup. |
| **sidecar** | LiteLLM runs as a separate container alongside the gateway (e.g., in the same Kubernetes pod). Requires `endpoint` to be set. | Kubernetes deployments where you want process isolation but low-latency communication. |
| **external** | LiteLLM runs as a standalone service elsewhere on the network. Requires `endpoint` to be set. | Multi-agent deployments sharing a single LiteLLM proxy, or when a dedicated team manages the LLM layer. |

For `sidecar` and `external` modes, you must provide the `llm.litellm.endpoint` URL (e.g., `http://litellm-proxy:4000`).

---

## How do I add OpenAPI tools?

1. Navigate to the [Tool Workshop]({{ site.baseurl }}/user/features/tools/) or the Tools section in the [Config Builder]({{ site.baseurl }}/user/features/config-builder/).
2. Click **Add Tool** and select the OpenAPI import option.
3. Provide the URL or file path to the OpenAPI (Swagger) specification.
4. Optionally filter by tags or operation IDs to import only specific endpoints.
5. Optionally set a namespace to prefix tool names and avoid collisions.
6. Save the configuration.

The spec is fetched and parsed into individual tools -- one per API operation. You can preview the tools before committing to see exactly what will be imported.

---

## Are my secrets safe in the config?

Yes. Forge AI uses a **secret reference** pattern -- the `forge.yaml` file contains pointers to where secrets are stored (environment variables or Kubernetes secrets), never the actual secret values.

When the admin API returns the config (e.g., in the Config Builder), all secret references are **redacted** and displayed as `***REDACTED***`. The control plane never transmits actual secret values to the browser.

At runtime, secrets are resolved by the `CompositeSecretResolver`, which reads the actual values from the configured source (environment variable or Kubernetes secret mount) inside the server process.

---

## What is AgentWeave?

AgentWeave is the security framework integrated into Forge AI for agent-to-agent (A2A) communication. It provides:

- **Identity** -- SPIFFE-based cryptographic identity so agents can prove who they are
- **Signing** -- message signing to ensure integrity (messages are not tampered with in transit)
- **Audit** -- logging of all agent interactions for compliance and debugging
- **Authorization** -- policy-based access control via OPA (Open Policy Agent)
- **Trust** -- configurable trust levels and policies for peer relationships

AgentWeave is enabled by default in the config (`security.agentweave.enabled: true`). When disabled, the gateway runs in development mode without identity verification.

For more details, see the [Security]({{ site.baseurl }}/user/features/security/) page.
