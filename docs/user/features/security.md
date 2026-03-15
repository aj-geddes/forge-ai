---
layout: page
title: "Security"
description: "Review and manage AgentWeave, rate limiting, CORS, and API key settings."
tier: user
nav_order: 8
---

# Security

The Security page gives you visibility into your agent's security configuration. It displays the current state of AgentWeave, rate limiting, CORS, and API key settings. To modify these settings, use the [Config Builder]({{ site.baseurl }}/user/features/config-builder/).

![Security settings]({{ site.baseurl }}/assets/images/screenshots/security.png)

## AgentWeave

AgentWeave is Forge AI's security framework for agent-to-agent communication. The Security page shows whether AgentWeave is **enabled** or **disabled**.

When enabled, AgentWeave provides:

| Feature | Description |
|---------|-------------|
| **Identity verification** | Each agent has a cryptographic identity (SPIFFE-based) that peers can verify. This prevents impersonation. |
| **Message signing** | Outgoing messages are signed so recipients can verify they were not tampered with in transit. |
| **Audit logging** | All agent-to-agent interactions are logged for compliance and debugging. |
| **Authorization** | An authorization provider (OPA by default) evaluates policies to decide whether a request should be allowed. |
| **Trust policy** | Either `strict` (deny by default, require explicit authorization) or `permissive` (allow by default). |

When AgentWeave is disabled, the gateway runs in **development mode** -- all requests are allowed without identity verification. A warning is logged at startup.

### AgentWeave settings displayed

| Setting | Description |
|---------|-------------|
| **Status** | Enabled or Disabled. |
| **Trust Domain** | The SPIFFE trust domain (e.g., `forge.local`). |
| **Trust Policy** | `strict` or `permissive`. |
| **Authorization Provider** | The authz backend (e.g., `opa`). |

## Rate Limiting

The rate limiting section shows the configured **requests per minute (RPM)** threshold. When an API consumer exceeds this limit, subsequent requests receive a `429 Too Many Requests` response until the rate window resets.

| Setting | Description |
|---------|-------------|
| **Requests per minute** | The maximum number of API requests allowed per minute. Default: `60`. |

Rate limiting applies to all incoming API requests (REST, conversational, and programmatic endpoints).

## Allowed Origins (CORS)

The allowed origins section displays the CORS (Cross-Origin Resource Sharing) configuration. This controls which browser origins are permitted to make requests to the Forge AI gateway.

| Setting | Description |
|---------|-------------|
| **Origins** | A list of allowed origins. A wildcard `*` means all origins are permitted (typical for development). In production, restrict this to your control plane domain. |

Examples:
- `*` -- allow all origins (development only)
- `https://forge.example.com` -- allow only your production domain
- `http://localhost:3000` -- allow a local development server

## API Keys

The API keys section shows the authentication configuration for the admin API.

| Setting | Description |
|---------|-------------|
| **Status** | Whether API key authentication is enabled or disabled. |
| **Key count** | The number of configured API keys. |
| **Key values** | Always displayed as `***REDACTED***` for security. You can see how many keys are configured and their source type (environment variable or Kubernetes secret), but never the actual key values. |

API keys can be provided via two sources:
- **Environment variables** (`source: env`) -- the key value is read from an environment variable at startup.
- **Kubernetes secrets** (`source: k8s_secret`) -- the key value is read from a Kubernetes secret, identified by secret name and key.

To add, remove, or rotate API keys, edit the `security.api_keys.keys` section in the [Config Builder]({{ site.baseurl }}/user/features/config-builder/) and update the corresponding environment variable or Kubernetes secret.
