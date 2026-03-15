---
layout: page
title: "Troubleshooting"
description: "Common errors and solutions for the Forge AI control plane."
tier: user
nav_order: 11
---

# Troubleshooting

This page covers common errors you may encounter in the Forge AI control plane and how to resolve them.

---

## 401 Unauthorized

**What you see:** The control plane shows an authentication error, or you are redirected to the login screen unexpectedly.

**Causes:**

- **Invalid API key.** The API key you entered does not match any of the keys configured on the server. Double-check the key value with your administrator.
- **Expired or rotated key.** If the API key was recently rotated (the environment variable or Kubernetes secret was updated), your stored key is no longer valid. The UI stores your key in `localStorage` and automatically logs you out when it receives a 401 response.
- **Missing credentials.** The request did not include an `Authorization: Bearer <key>` or `X-API-Key` header. This can happen if `localStorage` was cleared or you are using a different browser.

**Solutions:**

1. Log in again with the correct API key.
2. If you do not know the current key, ask your administrator.
3. Clear your browser's `localStorage` for the control plane URL and log in fresh.

---

## 403 Forbidden

**What you see:** The control plane displays "Admin API key authentication is not configured" or "No admin API keys are configured."

**Causes:**

- **API keys not enabled.** The `security.api_keys.enabled` field is set to `false` (the default). The gateway denies all admin requests when API key auth is disabled.
- **No keys defined.** The `security.api_keys.keys` list is empty or all secret references failed to resolve.

**Solutions:**

1. Enable API key authentication in your config:
   ```yaml
   security:
     api_keys:
       enabled: true
       keys:
         - source: env
           name: FORGE_API_KEY
   ```
2. Ensure the environment variable (e.g., `FORGE_API_KEY`) is set on the server.
3. Restart the gateway or wait for hot-reload.

---

## "Unable to load configuration data"

**What you see:** The Config Builder or Dashboard shows a message like "Unable to load configuration data" or fields appear empty.

**Causes:**

- **Authentication issue.** The admin API returned a 401 or 403, which the UI interprets as a config load failure. Check your API key.
- **Config not loaded.** The gateway started without a valid `forge.yaml` file. This happens if the file does not exist at the expected path or contains syntax errors.
- **Server unreachable.** The browser cannot reach the gateway API (network issue, wrong URL, or the server is down).

**Solutions:**

1. Verify your API key is correct by logging out and logging back in.
2. Check that `forge.yaml` exists at the path specified by the `FORGE_CONFIG_PATH` environment variable (defaults to `forge.yaml` in the working directory).
3. Validate your YAML syntax -- a misplaced indent or missing colon will prevent loading.
4. Check the gateway logs for specific error messages.

---

## Health shows "Degraded"

**What you see:** The dashboard Health Status badge shows **Degraded** instead of **Healthy**.

**Causes:**

A subsystem health check is failing. Common reasons include:

| Failing subsystem | Likely cause |
|-------------------|-------------|
| **LLM connection** | The LLM provider API is unreachable, the API key is invalid or expired, or the model name is not recognized. |
| **AgentWeave** | The SPIRE agent socket is not available, the OPA endpoint is down, or the identity secret is missing. |
| **Tool registry** | An OpenAPI spec URL is unreachable, or a tool definition has a validation error. |
| **Config watcher** | The config file was deleted or moved while the watcher was running. |

**Solutions:**

1. Open the **Health Checks** section on the Dashboard to identify which specific check is failing.
2. For LLM issues: verify your API key environment variables are set and the provider is accessible. Test with `curl` from the server.
3. For AgentWeave issues: if you do not need A2A security, set `security.agentweave.enabled: false`.
4. For tool issues: check that OpenAPI spec URLs are reachable from the server.
5. Check the gateway server logs for detailed error messages.

---

## Chat not responding

**What you see:** You send a message in the Chat interface but no response appears, or the response hangs indefinitely.

**Causes:**

- **LLM provider unavailable.** The configured model's API is down or unreachable from the server.
- **Invalid model configuration.** The `llm.default_model` does not match any entry in `llm.litellm.model_list`, or the model's API key is missing.
- **Timeout.** The LLM request exceeded the configured timeout (`llm.litellm.timeout`, default 30 seconds).
- **Max turns reached.** The session has exceeded the agent's `max_turns` limit.

**Solutions:**

1. Check the Dashboard health status -- if it shows Degraded, the LLM connection may be the issue.
2. Verify that the model name in `llm.default_model` matches a `model_name` in the `llm.litellm.model_list`.
3. Confirm the API key environment variable for your LLM provider is set and valid.
4. Try increasing `llm.litellm.timeout` if your queries are complex and require more processing time.
5. Start a new session if you have hit the turn limit.
6. Check the gateway server logs for error details from the LLM provider.

---

## Tools not loading

**What you see:** The Tool Workshop shows no tools, or tools you expected to see are missing.

**Causes:**

- **OpenAPI spec unreachable.** The URL specified in `tools.openapi_sources[].url` cannot be fetched from the server (DNS failure, firewall, or the spec host is down).
- **Spec parse error.** The OpenAPI spec has invalid syntax or uses unsupported features.
- **Tag/operation filter too restrictive.** The `include_tags` or `include_operations` filters exclude all operations.
- **Agent not initialized.** If the agent failed to initialize at startup (check logs), the tool registry will be empty.
- **Config reload failure.** A hot-reload attempt failed, leaving the tool surface in a stale state.

**Solutions:**

1. Verify the OpenAPI spec URL is accessible from the server (not just from your browser). Use `curl <url>` on the server to test.
2. Check for YAML/JSON syntax errors in the spec.
3. Remove or broaden `include_tags` and `include_operations` filters to ensure at least some operations match.
4. Check the gateway server logs for tool build errors during startup or reload.
5. Click **Reload** in the Config Builder to re-trigger tool loading.

---

## Peer shows "Unreachable"

**What you see:** Clicking the Ping button on a peer card shows "Unreachable" with an error message.

**Causes:**

- **Peer is down.** The peer Forge AI instance is not running.
- **Network issue.** The server cannot reach the peer endpoint (DNS, firewall, or routing).
- **Wrong endpoint.** The peer URL in the config is incorrect.
- **SSRF protection.** The peer endpoint targets a private or internal IP address, which is blocked by the gateway's SSRF protection.

**Solutions:**

1. Verify the peer is running and accessible from the Forge AI server.
2. Check the endpoint URL for typos.
3. If the peer is on an internal network, ensure the URL uses a routable hostname (not `localhost`, `127.0.0.1`, or addresses in `10.x.x.x`, `172.16.x.x`, `192.168.x.x` ranges).
4. Check the error message returned by the ping -- it includes the specific HTTP or connection error.
