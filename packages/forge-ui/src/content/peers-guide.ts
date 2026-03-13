import type { GuideSection } from "./index";

export const peersGuide: GuideSection = {
  id: "peers-guide",
  title: "Peers Guide",
  overview:
    "Understand the Agent-to-Agent (A2A) protocol, trust levels, and how to configure peer agents for multi-agent collaboration.",
  concepts: [
    {
      title: "A2A Protocol",
      description:
        "The Agent-to-Agent protocol lets Forge agents communicate with other agents. Peers can invoke each other's tools and exchange messages securely.",
      icon: "Network",
    },
    {
      title: "Trust Levels",
      description:
        "Each peer is assigned a trust level (high, medium, low) that determines what actions it can perform. High-trust peers have full access; low-trust peers are restricted.",
      icon: "ShieldCheck",
    },
    {
      title: "Peer Discovery",
      description:
        "Peers can be statically configured in forge.yaml or discovered dynamically via a registry. Each peer advertises its capabilities.",
      icon: "Search",
    },
    {
      title: "Signed Messages",
      description:
        "All A2A messages are cryptographically signed using AgentWeave identities. This ensures authenticity and prevents tampering.",
      icon: "KeyRound",
    },
  ],
  steps: [
    {
      title: "Register a peer",
      content:
        "Add a peer entry in your forge.yaml under the peers section. Specify the peer's URL, name, and trust level.",
    },
    {
      title: "Configure trust",
      content:
        "Set the trust level for each peer. High trust allows full tool access, medium trust allows read-only tools, and low trust allows only basic queries.",
    },
    {
      title: "Test connectivity",
      content:
        "Use the Peers page to verify connectivity to each registered peer. The status indicator shows if the peer is reachable.",
    },
    {
      title: "Monitor interactions",
      content:
        "Check the audit log on the Security page to see all A2A interactions, including which peer made what requests.",
    },
  ],
  examples: [
    {
      title: "Peer Configuration",
      language: "yaml",
      code: `peers:
  - name: research-agent
    url: https://research.internal:8000
    trust_level: high
    description: "Research and data analysis agent"
  - name: external-agent
    url: https://partner-api.example.com/agent
    trust_level: low
    description: "Third-party partner agent"`,
    },
    {
      title: "A2A Request",
      language: "bash",
      code: `curl -X POST http://localhost:8000/api/a2a/invoke \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Identity: <signed-token>" \\
  -d '{
    "peer": "research-agent",
    "tool": "analyze-data",
    "parameters": { "dataset": "q4-sales" }
  }'`,
    },
  ],
  tryIt: { label: "View Peers", path: "/peers" },
  related: ["security-guide", "config-reference"],
};
