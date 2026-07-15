import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToolsPage } from "./ToolsPage";

function renderToolsPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ToolsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const MANUAL_TOOL = {
  name: "get_weather",
  description: "Gets the current weather",
  parameters: [{ name: "city", type: "string", description: "City name", required: true }],
  api: {
    url: "https://api.example.com/v1/weather",
    method: "GET",
    headers: { "X-Api-Key": "***REDACTED***" },
    auth: { type: "api_key", token: { source: "env", name: "WEATHER_API_KEY" } },
    timeout: 30,
    response_mapping: { result_path: "$.data", field_map: {} },
  },
  requires_approval: false,
};

function configEnvelope(overrides: Record<string, unknown> = {}) {
  return {
    path: "forge.yaml",
    rev: 4,
    base_rev: "base-sha-1",
    drift_from_git: false,
    source_layers: ["base"],
    mutation_policy: "overlay",
    config: {
      metadata: { name: "forge", version: "0.1.0" },
      llm: { default_model: "gpt-4o" },
      tools: { openapi_sources: [], manual_tools: [MANUAL_TOOL], workflows: [] },
    },
    ...overrides,
  };
}

function stubFetch(handlers: {
  listTools?: () => unknown[];
  envelope?: () => ReturnType<typeof configEnvelope>;
  updateTool?: (name: string, body: Record<string, unknown>) => { status: number; body: unknown };
}) {
  const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const method = (options?.method ?? "GET").toUpperCase();

    if (url === "/v1/admin/tools" && method === "GET") {
      const body = handlers.listTools
        ? handlers.listTools()
        : [{ name: "get_weather", description: "Gets the current weather", source: "configured" }];
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/admin/config" && method === "GET") {
      const body = handlers.envelope ? handlers.envelope() : configEnvelope();
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url.startsWith("/v1/admin/tools/") && method === "PATCH") {
      const name = decodeURIComponent(url.replace("/v1/admin/tools/", ""));
      const parsedBody = JSON.parse(options?.body as string) as Record<string, unknown>;
      const { status, body } = handlers.updateTool
        ? handlers.updateTool(name, parsedBody)
        : {
            status: 200,
            body: {
              success: true,
              persisted: true,
              durable: true,
              drift_from_git: false,
              rev: 5,
              base_rev: "base-sha-1",
              promotion_available: false,
              message: "ok",
            },
          };
      return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
    }

    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  window.HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
    this.open = true;
  });
  window.HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
    this.open = false;
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ToolsPage edit dialog -- runtime edit never submits base-only fields", () => {
  it("edits description + parameters + response_mapping only, and never sends url/auth/headers/method/timeout/requires_approval", async () => {
    const fetchMock = stubFetch({});

    const user = userEvent.setup();
    renderToolsPage();

    await waitFor(() => expect(screen.getByText("get_weather")).toBeInTheDocument());

    const editButtons = screen.getAllByRole("button").filter((b) => b.querySelector("svg.lucide-pencil"));
    await user.click(editButtons[0]!);

    const editHeading = await screen.findByRole("heading", { name: /edit tool/i });
    const dialog = within(editHeading.closest("dialog") as HTMLElement);

    // The base-only panel is present with the promote affordance.
    expect(dialog.getByText(/managed in git/i)).toBeInTheDocument();
    const promoteLink = dialog.getByRole("link", { name: /review.*promote/i });
    expect(promoteLink).toHaveAttribute("href", "/config?tab=promote");

    // The URL is displayed read-only text, never inside an editable input.
    expect(dialog.getByText("https://api.example.com/v1/weather")).toBeInTheDocument();
    expect(dialog.queryByDisplayValue("https://api.example.com/v1/weather")).not.toBeInTheDocument();

    // Make a genuinely runtime-safe edit: change the description and add a parameter.
    const descriptionField = dialog.getByLabelText(/^description$/i);
    await user.clear(descriptionField);
    await user.type(descriptionField, "Gets weather by city name");

    await user.click(dialog.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        (call) =>
          typeof call[0] === "string" &&
          call[0].startsWith("/v1/admin/tools/") &&
          (call[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
    });

    const patchCall = fetchMock.mock.calls.find(
      (call) =>
        typeof call[0] === "string" &&
        call[0].startsWith("/v1/admin/tools/") &&
        (call[1] as RequestInit | undefined)?.method === "PATCH",
    )!;
    const [, init] = patchCall as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;

    // Only the runtime-safe fields are present.
    expect(Object.keys(body).sort()).toEqual(["api", "description", "parameters"].sort());
    expect(body.description).toBe("Gets weather by city name");

    // The base-only fields are structurally absent from the payload -- not
    // just unset, but never even a key the caller could set.
    expect(body).not.toHaveProperty("url");
    expect(body).not.toHaveProperty("base_url");
    expect(body).not.toHaveProperty("endpoint");
    expect(body).not.toHaveProperty("method");
    expect(body).not.toHaveProperty("headers");
    expect(body).not.toHaveProperty("auth");
    expect(body).not.toHaveProperty("timeout");
    expect(body).not.toHaveProperty("requires_approval");
    const api = body.api as Record<string, unknown>;
    expect(Object.keys(api)).toEqual(["response_mapping"]);
  });

  it("keeps the honesty envelope: a rejected write never closes the dialog", async () => {
    stubFetch({
      updateTool: () => ({
        status: 200,
        body: {
          success: false,
          persisted: false,
          durable: false,
          drift_from_git: true,
          rev: 4,
          base_rev: "base-sha-1",
          promotion_available: false,
          message: "overlay write rejected: read-only filesystem",
        },
      }),
    });

    const user = userEvent.setup();
    renderToolsPage();

    await waitFor(() => expect(screen.getByText("get_weather")).toBeInTheDocument());
    const editButtons = screen.getAllByRole("button").filter((b) => b.querySelector("svg.lucide-pencil"));
    await user.click(editButtons[0]!);

    const editHeading = await screen.findByRole("heading", { name: /edit tool/i });
    const dialog = within(editHeading.closest("dialog") as HTMLElement);

    await user.click(dialog.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      expect(screen.getByText(/overlay write rejected/i)).toBeInTheDocument();
    });
    expect(screen.getByRole("heading", { name: /edit tool/i })).toBeInTheDocument();
  });
});
