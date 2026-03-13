import type { GuideSection } from "./index";

export const gettingStarted: GuideSection = {
  id: "getting-started",
  title: "Getting Started",
  overview:
    "Learn how to set up and configure your first Forge AI agent deployment.",
  concepts: [
    {
      title: "What is Forge AI?",
      description:
        "Forge AI is a config-driven AI agent system that lets you define tools, connect to LLMs, and expose your agent through REST, MCP, and A2A interfaces.",
      icon: "Cpu",
    },
    {
      title: "Config-Driven Architecture",
      description:
        "Everything in Forge is configured via a single YAML file. Define your LLM, tools, security policies, and deployment settings declaratively.",
      icon: "FileText",
    },
    {
      title: "Tool Surfaces",
      description:
        "Tools are dynamically built from OpenAPI specs, manual definitions, or workflows. Your agent automatically discovers and uses the tools you configure.",
      icon: "Wrench",
    },
  ],
  steps: [
    {
      title: "Install Forge AI",
      content:
        "Clone the repository and install dependencies using uv:\n\ngit clone <repo-url>\ncd forge-ai\nuv sync",
    },
    {
      title: "Create your config file",
      content:
        "Copy forge.yaml.example to forge.yaml and customize it for your needs. The config file defines your agent's LLM, tools, and behavior.",
    },
    {
      title: "Start the gateway",
      content:
        "Run the gateway server to expose your agent:\n\nuvicorn forge_gateway.app:create_app --factory --host 0.0.0.0 --port 8000",
    },
    {
      title: "Open the Control Plane",
      content:
        "Navigate to http://localhost:8000 in your browser to access this UI. From here you can configure your agent, manage tools, and start chatting.",
    },
  ],
  examples: [
    {
      title: "Minimal Config",
      language: "yaml",
      code: `metadata:
  name: my-agent
  version: "0.1.0"

llm:
  default_model: gpt-4o
  temperature: 0.7`,
    },
    {
      title: "Config with a Tool",
      language: "yaml",
      code: `metadata:
  name: my-agent
  version: "0.1.0"

llm:
  default_model: gpt-4o

tools:
  openapi:
    - name: petstore
      spec_url: https://petstore3.swagger.io/api/v3/openapi.json`,
    },
  ],
  tryIt: { label: "Edit your config now", path: "/config" },
  related: ["config-reference", "tools-guide"],
};
