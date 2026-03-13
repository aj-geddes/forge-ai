import type { GuideSection } from "./index";

export const glossary: GuideSection = {
  id: "glossary",
  title: "Glossary",
  overview:
    "Key terms and definitions used throughout Forge AI.",
  concepts: [
    {
      title: "Agent",
      description:
        "An AI-powered entity that processes user messages, reasons about them, and can use tools to take actions. Configured via forge.yaml.",
      icon: "Bot",
    },
    {
      title: "Tool",
      description:
        "A callable function that an agent can invoke. Tools can be auto-generated from OpenAPI specs, manually defined, or composed as workflows.",
      icon: "Wrench",
    },
    {
      title: "Gateway",
      description:
        "The FastAPI server that exposes your agent through REST, MCP, and A2A interfaces. Entry point for all external communication.",
      icon: "Server",
    },
    {
      title: "MCP (Model Context Protocol)",
      description:
        "A protocol for providing contextual tools and resources to LLMs. Forge exposes tools as an MCP server using FastMCP.",
      icon: "Blocks",
    },
    {
      title: "A2A (Agent-to-Agent)",
      description:
        "A protocol for agents to communicate with each other. Enables multi-agent collaboration with trust and identity verification.",
      icon: "Network",
    },
    {
      title: "AgentWeave",
      description:
        "The security framework that provides cryptographic identity, message signing, audit logging, and trust management.",
      icon: "Shield",
    },
    {
      title: "LiteLLM",
      description:
        "An LLM routing library that provides a unified interface to multiple LLM providers. Forge uses it for model selection and fallback.",
      icon: "Zap",
    },
    {
      title: "PydanticAI",
      description:
        "The agent framework used by Forge to build AI agents with structured outputs, tool integration, and conversation management.",
      icon: "Cpu",
    },
    {
      title: "Trust Level",
      description:
        "A classification (high, medium, low) assigned to peer agents that determines what actions they can perform on your agent.",
      icon: "ShieldCheck",
    },
    {
      title: "Hot Reload",
      description:
        "The ability to update forge.yaml and have changes take effect without restarting the gateway. Supported for most configuration sections.",
      icon: "RefreshCw",
    },
    {
      title: "Control Plane",
      description:
        "This web UI. Provides a visual interface for configuring, monitoring, and interacting with your Forge AI agent.",
      icon: "LayoutDashboard",
    },
    {
      title: "System Prompt",
      description:
        "Instructions given to the LLM that define the agent's persona, behavior, and constraints. Set per-agent in forge.yaml.",
      icon: "FileText",
    },
  ],
  steps: [],
  examples: [],
  related: ["getting-started", "config-reference"],
};
