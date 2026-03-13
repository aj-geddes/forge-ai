import type { GuideSection } from "./index";

export const securityGuide: GuideSection = {
  id: "security-guide",
  title: "Security Guide",
  overview:
    "Learn about Forge AI's security features including AgentWeave integration, rate limiting, API key management, and trust policies.",
  concepts: [
    {
      title: "AgentWeave",
      description:
        "AgentWeave provides cryptographic identity, message signing, audit logging, and trust management for your agent.",
      icon: "Fingerprint",
    },
    {
      title: "Rate Limiting",
      description:
        "Protect your agent from abuse with configurable rate limits. Set requests per minute, burst limits, and per-client quotas.",
      icon: "Gauge",
    },
    {
      title: "API Key Management",
      description:
        "Create and manage API keys with scoped permissions. Keys can be restricted to specific endpoints or tools.",
      icon: "Key",
    },
    {
      title: "Trust Policies",
      description:
        "Define fine-grained trust policies that control which actions are allowed based on the requester's identity and trust level.",
      icon: "Scale",
    },
    {
      title: "Audit Logging",
      description:
        "Every action is logged with full context: who requested it, what tools were used, and what the outcome was.",
      icon: "ScrollText",
    },
  ],
  steps: [
    {
      title: "Enable AgentWeave",
      content:
        "Set security.agentweave.enabled to true in your config. On first run, an identity keypair will be generated automatically.",
    },
    {
      title: "Configure rate limits",
      content:
        "Set requests_per_minute and burst values under security.rate_limit. These apply globally; per-client limits can be set separately.",
    },
    {
      title: "Create API keys",
      content:
        "Add API key entries under security.api_keys. Each key has a name, the key value (use secret references), and a list of allowed scopes.",
    },
    {
      title: "Set trust policies",
      content:
        "Define trust policies that map identity attributes to allowed actions. For example, only high-trust peers can invoke write tools.",
    },
    {
      title: "Review audit logs",
      content:
        "Visit the Security page to view the audit log. Filter by time range, action type, or requester identity.",
    },
  ],
  examples: [
    {
      title: "Full Security Config",
      language: "yaml",
      code: `security:
  agentweave:
    enabled: true
    identity_file: ./identity.pem
    audit:
      enabled: true
      retention_days: 90
  rate_limit:
    requests_per_minute: 60
    burst: 10
    per_client:
      requests_per_minute: 20
  api_keys:
    - name: frontend
      key: \${FRONTEND_API_KEY}
      scopes: [chat, tools.read]
    - name: admin
      key: \${ADMIN_API_KEY}
      scopes: [chat, tools, config, security]`,
    },
    {
      title: "Trust Policy",
      language: "yaml",
      code: `security:
  trust_policies:
    - name: allow-high-trust-writes
      match:
        trust_level: high
      allow:
        - tools.*
        - config.read
    - name: restrict-low-trust
      match:
        trust_level: low
      allow:
        - chat
        - tools.read`,
    },
  ],
  tryIt: { label: "View Security Dashboard", path: "/security" },
  related: ["peers-guide", "config-reference"],
};
