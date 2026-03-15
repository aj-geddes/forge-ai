---
layout: page
title: "Tool Workshop"
description: "Add, manage, and preview tools in the Forge AI control plane."
tier: user
nav_order: 5
---

# Tool Workshop

The Tool Workshop is where you manage the tools available to your agent. Tools are capabilities the agent can invoke during conversations -- calling APIs, querying databases, or running multi-step workflows.

![Tool Workshop]({{ site.baseurl }}/assets/images/screenshots/tools.png)

## Tool types

Forge AI supports three types of tools:

### OpenAPI tools

Tools auto-generated from an OpenAPI (Swagger) specification. You provide a spec URL or file path, and Forge AI parses it into individual tools -- one per API operation. You can filter which operations to import using tags or operation IDs, and you can rename operations using a route map.

**Best for:** Connecting to existing REST APIs with published specifications.

### Manual tools

Custom-defined tools where you specify the name, description, parameters, and API call configuration explicitly. Each manual tool maps to a single HTTP request with configurable authentication, headers, body templates, and response mapping.

**Best for:** APIs without an OpenAPI spec, or when you need fine-grained control over how the tool calls an endpoint.

### Workflow tools

Composite tools that chain multiple tool calls into a sequence. Each step calls an existing tool, captures its output, and optionally passes data to the next step. Steps can be conditional, executing only when a specified condition is met.

**Best for:** Multi-step processes like "look up a contact, then enrich with weather data for their city."

## Adding tools

Click the **Add Tool** button at the top of the Tool Workshop to open the tool creation dialog. You will choose from the available tool types:

### Adding an OpenAPI source

1. Click **Add Tool** and select the OpenAPI import option.
2. Enter a **name** for the source (e.g., `petstore`).
3. Provide the **URL** of the OpenAPI specification (e.g., `https://petstore3.swagger.io/api/v3/openapi.json`).
4. Optionally set a **namespace** to prefix all tool names from this source.
5. Optionally filter by **tags** or **operation IDs** to import only specific endpoints.
6. Configure **authentication** if the API requires it (bearer token, API key, or basic auth).
7. Save. The spec is fetched and parsed, and the resulting tools appear in the tool list.

### Adding a manual tool

1. Click **Add Tool** and select the manual tool option.
2. Enter a **name** and **description**.
3. Define **parameters** -- each with a name, type (string, integer, number, boolean, array, object), description, and whether it is required.
4. Configure the **API call**: base URL, endpoint path, HTTP method, headers, authentication, and response mapping.
5. Save. The tool is added to the configuration and becomes available to the agent.

### Adding a workflow

1. Click **Add Tool** and select the workflow builder.
2. Enter a **name** and **description** for the workflow.
3. Define **input parameters** that the workflow accepts.
4. Add **steps**, each referencing an existing tool by name. For each step:
   - Map input parameters or previous step outputs to the tool's parameters using template syntax (e.g., `{{ "{{ email }}" }}`).
   - Optionally assign an **output name** to capture the step's result for use in later steps.
   - Optionally add a **condition** that must be true for the step to execute.
5. Save. The workflow appears as a single tool that the agent can call.

## Searching and filtering

Use the **search bar** at the top of the tool list to filter tools by name or description. This is helpful when your agent has many tools registered.

## Tool preview

The Tool Workshop supports a **preview** capability for OpenAPI sources. When you provide a spec URL, you can preview the tools that will be generated before committing the source to your configuration. This lets you verify that the correct operations are being imported and that naming is correct.

The preview sends the spec to the server for parsing but does not register the tools -- it is a dry-run.

## Managing existing tools

Existing tools are displayed in a list showing:

- **Name** -- the tool's identifier as the agent sees it
- **Description** -- a brief summary of what the tool does
- **Source** -- where the tool came from (OpenAPI spec, manual definition, or workflow)

To edit or remove a tool, use the [Config Builder]({{ site.baseurl }}/user/features/config-builder/) to modify the relevant section under `tools`.
