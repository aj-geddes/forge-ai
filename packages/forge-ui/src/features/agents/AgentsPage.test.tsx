import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentsPage } from "./AgentsPage";
import { Toaster } from "@/components/ui/toast";

function renderAgentsPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AgentsPage />
      <Toaster />
    </QueryClientProvider>,
  );
}

const CONFIG_ENVELOPE_BASE = {
  path: "forge.yaml",
  rev: 4,
  base_rev: "base-sha-1",
  drift_from_git: false,
  source_layers: ["base"],
  mutation_policy: "overlay",
};

function configEnvelope(overrides: Partial<typeof CONFIG_ENVELOPE_BASE> & { agents?: unknown[] } = {}) {
  const { agents = [], ...rest } = overrides;
  return {
    ...CONFIG_ENVELOPE_BASE,
    ...rest,
    config: {
      metadata: { name: "forge", version: "0.1.0" },
      llm: {
        default_model: "gpt-4o",
        litellm: {
          mode: "embedded",
          model_list: [{ model_name: "primary", litellm_params: { model: "openai/gpt-4o" } }],
        },
      },
      tools: { openapi_sources: [], manual_tools: [], workflows: [] },
      agents: { default: "assistant", agents, peers: [] },
    },
  };
}

/** Routes a stubbed fetch by method + path, matching this test file's needs. */
function stubFetch(handlers: {
  listAgents?: () => unknown[];
  envelope?: () => ReturnType<typeof configEnvelope>;
  listTools?: () => unknown[];
  createAgent?: (body: Record<string, unknown>) => { status: number; body: unknown };
  updateAgent?: (name: string, body: Record<string, unknown>) => { status: number; body: unknown };
  deleteAgent?: (name: string) => { status: number; body: unknown };
}) {
  const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const method = (options?.method ?? "GET").toUpperCase();

    if (url === "/v1/admin/agents" && method === "GET") {
      const body = handlers.listAgents ? handlers.listAgents() : [];
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/admin/config" && method === "GET") {
      const body = handlers.envelope ? handlers.envelope() : configEnvelope();
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/admin/tools" && method === "GET") {
      const body = handlers.listTools ? handlers.listTools() : [];
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/admin/agents" && method === "POST") {
      const parsedBody = JSON.parse(options?.body as string) as Record<string, unknown>;
      const { status, body } = handlers.createAgent
        ? handlers.createAgent(parsedBody)
        : {
            status: 201,
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

    if (url.startsWith("/v1/admin/agents/") && method === "PATCH") {
      const name = decodeURIComponent(url.replace("/v1/admin/agents/", ""));
      const parsedBody = JSON.parse(options?.body as string) as Record<string, unknown>;
      const { status, body } = handlers.updateAgent
        ? handlers.updateAgent(name, parsedBody)
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

    if (url.startsWith("/v1/admin/agents/") && method === "DELETE") {
      const name = decodeURIComponent(url.replace("/v1/admin/agents/", ""));
      const { status, body } = handlers.deleteAgent
        ? handlers.deleteAgent(name)
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

describe("AgentsPage create (POST /v1/admin/agents)", () => {
  it("creates an agent via the overlay mutation and surfaces success-drift when not yet in Git", async () => {
    let agents: unknown[] = [];
    stubFetch({
      listAgents: () => agents,
      envelope: () => configEnvelope({ agents }),
      createAgent: (body) => {
        const created = { ...body };
        agents = [...agents, created];
        return {
          status: 201,
          body: {
            success: true,
            persisted: true,
            durable: true,
            drift_from_git: true,
            rev: 5,
            base_rev: "base-sha-1",
            promotion_available: true,
            message: "ok",
          },
        };
      },
    });

    const user = userEvent.setup();
    renderAgentsPage();

    await waitFor(() => expect(screen.getByText(/no agents defined yet/i)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /new agent/i }));

    const createHeading = await screen.findByRole("heading", { name: /create agent/i });
    // Both the Create and Edit <dialog> elements are always mounted (only
    // native `open` toggles visibility), so scope every subsequent query to
    // this dialog -- otherwise a shared label like "Name" matches both.
    const createDialog = within(createHeading.closest("dialog") as HTMLElement);

    await user.type(createDialog.getByLabelText(/^name$/i), "researcher");
    await user.type(createDialog.getByLabelText(/system prompt/i), "You are a researcher.");

    await user.click(createDialog.getByRole("button", { name: /create agent/i }));

    // The overlay mutation call was actually made -- this is the load-bearing
    // assertion that agent creation persists via the overlay, not a local-only edit.
    await waitFor(() => {
      const postCall = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.find(
        (call) => call[0] === "/v1/admin/agents" && (call[1] as RequestInit | undefined)?.method === "POST",
      );
      expect(postCall).toBeDefined();
    });

    // Because persisted:true but drift_from_git:true, the UI must show the
    // "not yet in Git" drift affordance -- never a bare "success".
    await waitFor(() => {
      expect(screen.getAllByText(/saved.*not yet in git/i).length).toBeGreaterThan(0);
    });

    // The new agent shows up in the list once the query invalidates.
    await waitFor(() => expect(screen.getByText("researcher")).toBeInTheDocument());
  });

  it("never claims success and keeps the dialog open when persisted is false", async () => {
    stubFetch({
      listAgents: () => [],
      createAgent: () => ({
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
    renderAgentsPage();

    await waitFor(() => expect(screen.getByText(/no agents defined yet/i)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /new agent/i }));

    const createHeading = await screen.findByRole("heading", { name: /create agent/i });
    const createDialog = within(createHeading.closest("dialog") as HTMLElement);

    await user.type(createDialog.getByLabelText(/^name$/i), "researcher");
    await user.click(createDialog.getByRole("button", { name: /create agent/i }));

    await waitFor(() => {
      expect(screen.getByText(/overlay write rejected/i)).toBeInTheDocument();
    });

    // The dialog must still be open (form still visible) -- a rejected write
    // is never treated as a close-worthy success.
    expect(screen.getByRole("heading", { name: /create agent/i })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "researcher" })).not.toBeInTheDocument();
  });
});

describe("AgentsPage edit (PATCH /v1/admin/agents/{name})", () => {
  it("edits an agent's model/tools/max_turns via the overlay mutation", async () => {
    let agents: unknown[] = [
      {
        name: "researcher",
        description: "Researches topics",
        system_prompt: "You are a researcher.",
        model: "gpt-4o",
        tools: [],
        max_turns: 10,
        mode: "passive",
      },
    ];
    const fetchMock = stubFetch({
      listAgents: () => agents,
      envelope: () => configEnvelope({ agents }),
      listTools: () => [{ name: "web_search", description: "Search the web", source: "manual" }],
      updateAgent: (name, body) => {
        agents = agents.map((a) =>
          (a as { name: string }).name === name ? { ...(a as Record<string, unknown>), ...body } : a,
        );
        return {
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
      },
    });

    const user = userEvent.setup();
    renderAgentsPage();

    await waitFor(() => expect(screen.getByText("researcher")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /edit agent/i }));

    const editHeading = await screen.findByRole("heading", { name: /edit agent/i });
    // Both the Create and Edit <dialog> elements are always mounted (only
    // native `open` toggles visibility), so scope every subsequent query to
    // this dialog -- otherwise a shared label like "web_search" matches both.
    const editDialog = within(editHeading.closest("dialog") as HTMLElement);

    await user.click(editDialog.getByLabelText(/web_search/i));

    const maxTurnsInput = editDialog.getByLabelText(/max turns/i);
    await user.clear(maxTurnsInput);
    await user.type(maxTurnsInput, "20");

    await user.click(editDialog.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const patchCall = fetchMock.mock.calls.find(
        (call) =>
          typeof call[0] === "string" &&
          call[0].startsWith("/v1/admin/agents/") &&
          (call[1] as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patchCall).toBeDefined();
    });

    const patchCall = fetchMock.mock.calls.find(
      (call) =>
        typeof call[0] === "string" &&
        call[0].startsWith("/v1/admin/agents/") &&
        (call[1] as RequestInit | undefined)?.method === "PATCH",
    )!;
    const [, init] = patchCall as [string, RequestInit];
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.tools).toEqual(["web_search"]);
    expect(body.max_turns).toBe(20);

    // The dialog closes on a genuine persisted success.
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: /edit agent/i })).not.toBeInTheDocument();
    });
  });

  it("the model dropdown offers the configured model_list aliases", async () => {
    stubFetch({
      listAgents: () => [
        {
          name: "researcher",
          tools: [],
          mode: "passive",
        },
      ],
      envelope: () => configEnvelope({ agents: [] }),
    });

    const user = userEvent.setup();
    renderAgentsPage();

    await waitFor(() => expect(screen.getByText("researcher")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /edit agent/i }));

    const editHeading = await screen.findByRole("heading", { name: /edit agent/i });
    const editDialog = within(editHeading.closest("dialog") as HTMLElement);

    const modelSelect = editDialog.getByLabelText(/^model$/i) as HTMLSelectElement;
    const optionValues = Array.from(modelSelect.options).map((o) => o.value);
    expect(optionValues).toContain("primary");
  });
});

describe("AgentsPage delete (DELETE /v1/admin/agents/{name})", () => {
  it("deletes an agent after confirmation and removes it from the list", async () => {
    let agents: unknown[] = [{ name: "researcher", tools: [], mode: "passive" }];
    stubFetch({
      listAgents: () => agents,
      envelope: () => configEnvelope({ agents }),
      deleteAgent: (name) => {
        agents = agents.filter((a) => (a as { name: string }).name !== name);
        return {
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
      },
    });

    const user = userEvent.setup();
    renderAgentsPage();

    await waitFor(() => expect(screen.getByText("researcher")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /delete agent/i }));

    // Confirm inside the confirmation dialog specifically.
    await waitFor(() => {
      expect(screen.getByText(/delete agent\?/i)).toBeInTheDocument();
    });
    const confirmButtons = screen.getAllByRole("button", { name: /delete agent/i });
    await user.click(confirmButtons[confirmButtons.length - 1]!);

    await waitFor(() => expect(screen.getByText(/no agents defined yet/i)).toBeInTheDocument());
  });
});

describe("AgentsPage disabled (mutation_policy: disabled)", () => {
  it("disables create/edit/delete actions and never fires a mutation", async () => {
    stubFetch({
      listAgents: () => [{ name: "researcher", tools: [], mode: "passive" }],
      envelope: () => configEnvelope({ agents: [], mutation_policy: "disabled" }),
    });

    renderAgentsPage();

    await waitFor(() => expect(screen.getByText("researcher")).toBeInTheDocument());

    expect(screen.getByRole("button", { name: /new agent/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /edit agent/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /delete agent/i })).toBeDisabled();
  });
});
