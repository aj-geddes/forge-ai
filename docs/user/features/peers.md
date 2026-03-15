---
layout: page
title: "Peers"
description: "Manage agent-to-agent (A2A) peer connections in the Forge AI control plane."
tier: user
nav_order: 7
---

# Peers

The Peers page lets you manage agent-to-agent (A2A) connections between your Forge AI instance and other compatible agents. Peers enable your agent to delegate tasks, share capabilities, and collaborate with other agents in a distributed network.

![Peers]({{ site.baseurl }}/assets/images/screenshots/peers.png)

## What are peers?

A peer is another Forge AI instance (or any agent that implements the A2A protocol) that your agent can communicate with. When peers are configured, your agent can:

- Discover the other agent's capabilities through its A2A agent card
- Send requests to the peer to perform tasks your agent cannot handle locally
- Receive requests from peers (if they have your agent configured as a peer)

Each peer connection is defined by a name, endpoint URL, trust level, and a list of capabilities.

## Peer cards

Each peer is displayed as a card showing:

| Field | Description |
|-------|-------------|
| **Name** | The peer's identifier (e.g., `data-forge`, `security-forge`). |
| **Endpoint** | The URL of the peer's gateway (e.g., `https://data-forge.hvs.internal`). |
| **Trust Level** | The trust classification assigned to this peer: **high**, **medium**, or **low**. Displayed as a colored badge. |
| **Capabilities** | Badges showing what the peer can do (e.g., `data_query`, `reporting`, `threat_analysis`). |

## Adding a peer

Click the **Add Peer** button to open the peer creation dialog. Fill in:

1. **Name** -- a unique identifier for the peer.
2. **Endpoint** -- the full URL of the peer's gateway (e.g., `https://other-forge.example.com`). The endpoint must be reachable from your Forge AI instance.
3. **Trust Level** -- select `high`, `medium`, or `low` (see Trust Levels below).
4. **Capabilities** -- a list of capability tags describing what the peer can do.

Click Save to add the peer to your configuration. The peer appears in the peer list immediately.

**Note:** Peer endpoints that target private or internal IP addresses (e.g., `127.0.0.1`, `10.x.x.x`, `192.168.x.x`) or internal hostnames (e.g., `localhost`, `*.local`) are blocked by SSRF protection. Use routable hostnames or public IPs.

## Pinging a peer

Each peer card has a **Ping** button. Clicking it sends a health check request (`/health/live`) to the peer's endpoint and reports:

- **Reachable** -- the peer responded successfully. The latency of the request is displayed so you can gauge network performance.
- **Unreachable** -- the peer did not respond or returned an error. The error details are shown.

Use ping to verify that a peer is online and accessible before relying on it for agent-to-agent communication.

## Trust levels

Trust levels control how much your agent trusts a peer's requests and responses:

| Level | Meaning | Use case |
|-------|---------|----------|
| **High** | Full trust. The peer is treated as a first-party agent with minimal restrictions. | Internal agents within the same organization or trust domain. |
| **Medium** | Moderate trust. Some verification and rate limiting may apply. | Partner agents or agents in a shared but not fully controlled environment. |
| **Low** | Minimal trust. Strict verification, rate limiting, and capability restrictions apply. | External or third-party agents where you want maximum caution. |

Trust levels work alongside AgentWeave's trust policy (`strict` or `permissive`) to determine the actual security posture for each peer connection. See [Security]({{ site.baseurl }}/user/features/security/) for more.

## Removing a peer

To remove a peer, use the [Config Builder]({{ site.baseurl }}/user/features/config-builder/) to delete the peer entry from the `agents.peers` section. After saving, the peer disappears from the Peers page.
