import type { GuideSection } from "./index";

export const troubleshooting: GuideSection = {
  id: "troubleshooting",
  title: "Troubleshooting",
  overview:
    "Common issues and solutions for Forge AI. Check here first if something is not working as expected.",
  concepts: [
    {
      title: "Health Checks",
      description:
        "The health indicator in the header shows the gateway status. If it shows unhealthy, check that the gateway process is running.",
      icon: "HeartPulse",
    },
    {
      title: "Config Validation",
      description:
        "Forge validates your YAML config on startup. Validation errors appear in the gateway logs with details about the invalid field.",
      icon: "AlertTriangle",
    },
    {
      title: "Connection Issues",
      description:
        "If the UI cannot connect to the gateway, verify the gateway URL and check for CORS configuration or network issues.",
      icon: "Unplug",
    },
  ],
  steps: [
    {
      title: "Check gateway logs",
      content:
        "Look at the terminal where the gateway is running. Error messages include the file, line, and details of what went wrong.",
    },
    {
      title: "Validate your config",
      content:
        "Run 'uv run python -m forge_config.validate forge.yaml' to check your config file for errors without starting the gateway.",
    },
    {
      title: "Test API connectivity",
      content:
        "Run 'curl http://localhost:8000/health' to verify the gateway is reachable and responding.",
    },
    {
      title: "Check environment variables",
      content:
        "Ensure all secret references (${VAR_NAME}) in your config resolve to actual environment variables.",
    },
  ],
  examples: [
    {
      title: "Health Check",
      language: "bash",
      code: `curl http://localhost:8000/health
# Expected: {"status": "ok", "version": "1.0.0"}`,
    },
    {
      title: "Config Validation",
      language: "bash",
      code: `uv run python -m forge_config.validate forge.yaml
# Expected: "Config is valid" or detailed error messages`,
    },
  ],
  troubleshooting: [
    {
      question: "The health indicator shows 'Unhealthy'",
      answer:
        "Verify the gateway is running with 'ps aux | grep uvicorn'. If it crashed, check the terminal output for error details. Common causes: invalid config, missing API keys, or port already in use.",
    },
    {
      question: "My tools are not showing up",
      answer:
        "Check that your tools section in forge.yaml is correctly formatted. OpenAPI spec URLs must be reachable from the gateway. Run with --log-level debug for detailed tool loading output.",
    },
    {
      question: "Chat responses are empty or erroring",
      answer:
        "Verify your LLM API key is set correctly. Check that the model name matches your provider's expected format. Try a simpler prompt to isolate the issue.",
    },
    {
      question: "Peer agents cannot connect",
      answer:
        "Ensure both agents have AgentWeave enabled and can reach each other's URLs. Check that trust levels are configured correctly and that the peer's identity is recognized.",
    },
    {
      question: "Rate limiting is blocking legitimate requests",
      answer:
        "Increase the requests_per_minute or burst values in your security.rate_limit config. Consider setting higher per-client limits for trusted API keys.",
    },
    {
      question: "The UI is not loading or shows a blank page",
      answer:
        "Clear your browser cache and reload. Check the browser console for JavaScript errors. Verify the gateway is serving the frontend files correctly.",
    },
    {
      question: "Config changes are not taking effect",
      answer:
        "Forge supports hot-reload for most config sections. If changes are not reflected, restart the gateway. Some changes (like security identity) require a full restart.",
    },
  ],
  related: ["getting-started", "config-reference", "security-guide"],
};
