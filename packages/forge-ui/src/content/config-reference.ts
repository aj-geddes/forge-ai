import type { GuideSection } from "./index";

export const configReference: GuideSection = {
  id: "config-reference",
  title: "Config Reference",
  overview:
    "Complete reference for all forge.yaml configuration sections. Every aspect of your agent is controlled through this file.",
  concepts: [
    {
      title: "Metadata",
      description:
        "The metadata section defines your agent's name, version, and description. These values appear in health checks and peer discovery.",
      icon: "Tag",
    },
    {
      title: "LLM Settings",
      description:
        "Configure which language model to use, temperature, max tokens, and routing via LiteLLM. Supports OpenAI, Anthropic, and local models.",
      icon: "Brain",
    },
    {
      title: "Tools Configuration",
      description:
        "Define OpenAPI sources, manual tools, and workflow chains. Tools are automatically loaded and provided to the agent at runtime.",
      icon: "Wrench",
    },
    {
      title: "Security Policies",
      description:
        "Configure AgentWeave integration, API keys, rate limiting, and trust levels for peer agents.",
      icon: "Shield",
    },
    {
      title: "Agents Section",
      description:
        "Define one or more named agents, each with their own system prompt, tools subset, and behavioral parameters.",
      icon: "Bot",
    },
  ],
  steps: [
    {
      title: "Start with metadata",
      content:
        "Every config file must begin with a metadata block containing at minimum a name and version.",
    },
    {
      title: "Configure your LLM",
      content:
        "Set the default_model to the model identifier your provider expects (e.g., gpt-4o, claude-3-opus). Add your API key via environment variable or secret reference.",
    },
    {
      title: "Add tools",
      content:
        "Under the tools section, add openapi entries with spec URLs, manual tool definitions with HTTP endpoints, or workflow chains that combine multiple tools.",
    },
    {
      title: "Set security policies",
      content:
        "Enable AgentWeave for cryptographic identity and audit logging. Configure rate limits and trust levels for incoming peer requests.",
    },
    {
      title: "Define agents",
      content:
        "Create named agents under the agents section. Each agent can have a custom system prompt and a subset of available tools.",
    },
  ],
  examples: [
    {
      title: "Full Metadata Block",
      language: "yaml",
      code: `metadata:
  name: my-production-agent
  version: "1.0.0"
  description: "Customer support agent with tool access"
  environment: production`,
    },
    {
      title: "LLM with Fallback",
      language: "yaml",
      code: `llm:
  default_model: gpt-4o
  fallback_model: gpt-3.5-turbo
  temperature: 0.7
  max_tokens: 4096
  api_key: \${OPENAI_API_KEY}`,
    },
    {
      title: "Security Configuration",
      language: "yaml",
      code: `security:
  agentweave:
    enabled: true
    identity_file: ./identity.pem
  rate_limit:
    requests_per_minute: 60
    burst: 10
  api_keys:
    - name: frontend
      key: \${FRONTEND_API_KEY}
      scopes: [chat, tools]`,
    },
    {
      title: "Agent Definition",
      language: "yaml",
      code: `agents:
  - name: support-agent
    system_prompt: |
      You are a helpful customer support agent.
      Use the available tools to look up orders and accounts.
    tools:
      - petstore
      - order-lookup
    temperature: 0.5`,
    },
  ],
  tryIt: { label: "Open Config Builder", path: "/config" },
  related: ["getting-started", "tools-guide", "security-guide"],
};
