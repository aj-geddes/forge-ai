import type { GuideSection } from "./index";

export const toolsGuide: GuideSection = {
  id: "tools-guide",
  title: "Tools Guide",
  overview:
    "Learn how to configure and use the three types of tools in Forge AI: OpenAPI sources, manual tools, and workflows.",
  concepts: [
    {
      title: "OpenAPI Tools",
      description:
        "Automatically generate tools from any OpenAPI/Swagger specification. Forge parses the spec, extracts operations, and makes them available to the agent.",
      icon: "Globe",
    },
    {
      title: "Manual Tools",
      description:
        "Define custom HTTP-based tools with explicit request/response schemas. Useful for internal APIs without OpenAPI specs.",
      icon: "Hammer",
    },
    {
      title: "Workflow Tools",
      description:
        "Chain multiple tools into multi-step workflows with conditional logic. Output from one step feeds into the next.",
      icon: "GitBranch",
    },
    {
      title: "Tool Workshop",
      description:
        "The Tool Workshop UI lets you import, test, and preview tools interactively before adding them to your config.",
      icon: "FlaskConical",
    },
  ],
  steps: [
    {
      title: "Add an OpenAPI source",
      content:
        "In the Tool Workshop, click 'Import OpenAPI' and paste the URL to an OpenAPI spec. Forge will fetch and parse it, showing you all available operations.",
    },
    {
      title: "Create a manual tool",
      content:
        "Click 'Create Manual Tool' to define a custom tool. Specify the HTTP method, URL, headers, request body schema, and response schema.",
    },
    {
      title: "Build a workflow",
      content:
        "Use the Workflow Composer to chain tools together. Drag tools into a sequence, map outputs to inputs, and add conditional branches.",
    },
    {
      title: "Test your tools",
      content:
        "Use the built-in test panel to send requests to your tools and verify they work correctly before deploying.",
    },
    {
      title: "Export to config",
      content:
        "Once your tools are configured and tested, export the YAML snippet to add to your forge.yaml file.",
    },
  ],
  examples: [
    {
      title: "OpenAPI Tool Config",
      language: "yaml",
      code: `tools:
  openapi:
    - name: petstore
      spec_url: https://petstore3.swagger.io/api/v3/openapi.json
      operations:
        - getPetById
        - findPetsByStatus
      auth:
        type: bearer
        token: \${PETSTORE_TOKEN}`,
    },
    {
      title: "Manual Tool Config",
      language: "yaml",
      code: `tools:
  manual:
    - name: weather-lookup
      description: "Get current weather for a city"
      method: GET
      url: "https://api.weather.com/v1/current"
      parameters:
        - name: city
          in: query
          required: true
          schema:
            type: string`,
    },
    {
      title: "Workflow Config",
      language: "yaml",
      code: `tools:
  workflows:
    - name: enrich-and-notify
      description: "Look up customer, enrich data, send notification"
      steps:
        - tool: customer-lookup
          output: customer
        - tool: data-enrichment
          input:
            email: \${customer.email}
          output: enriched
        - tool: send-notification
          input:
            to: \${enriched.email}
            message: "Profile updated"`,
    },
  ],
  tryIt: { label: "Open Tool Workshop", path: "/tools" },
  related: ["config-reference", "getting-started", "chat-guide"],
};
