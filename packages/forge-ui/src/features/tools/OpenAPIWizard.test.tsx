import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { OpenAPIWizard } from "./OpenAPIWizard";
import { useToolStore } from "@/stores/toolStore";

function renderWizard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpenAPIWizard />
    </QueryClientProvider>,
  );
}

describe("OpenAPIWizard bearer auth payload", () => {
  beforeEach(() => {
    window.HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) {
      this.open = true;
    });
    window.HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) {
      this.open = false;
    });
    useToolStore.setState({
      wizardType: "openapi",
      step: 4,
      openApiData: {
        specUrl: "https://api.example.com/openapi.json",
        specTitle: "Example API",
        specVersion: "1.0.0",
        operations: [
          { operationId: "listWidgets", method: "GET", path: "/widgets", summary: "" },
        ],
        selected: ["listWidgets"],
        namespace: "",
        auth: {
          type: "none",
          token: "",
          headerName: "X-API-Key",
          username: "",
          password: "",
        },
      },
      manualData: useToolStore.getState().manualData,
      workflowData: useToolStore.getState().workflowData,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("emits a SecretRef ({source: 'env', name}) for bearer auth, never a raw token string", async () => {
    // AuthConfig.token on the backend (forge_config/schema.py) is `SecretRef | None`,
    // not a plain string -- PUT /v1/admin/config 400s on a literal token value.
    const getConfigResponse = {
      config: {
        metadata: { name: "forge", version: "0.1.0" },
        llm: { default_model: "gpt-4o" },
        tools: { openapi_sources: [], manual_tools: [], workflows: [] },
      },
      path: "forge.yaml",
    };

    const putBodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((_url: string, options?: RequestInit) => {
        if (options?.method === "PUT") {
          putBodies.push(JSON.parse(options.body as string));
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => ({ success: true, reloaded: false, message: "ok" }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => getConfigResponse,
        });
      }),
    );

    useToolStore.setState((state) => ({
      openApiData: {
        ...state.openApiData,
        auth: { ...state.openApiData.auth, type: "bearer", token: "MY_API_TOKEN" },
      },
    }));

    renderWizard();

    await userEvent.click(screen.getByRole("button", { name: "Add" }));

    await vi.waitFor(() => expect(putBodies.length).toBeGreaterThan(0));

    const body = putBodies[0] as {
      config: { tools: { openapi_sources: Array<{ auth?: { token?: unknown } }> } };
    };
    const source = body.config.tools.openapi_sources[0];
    expect(source?.auth?.token).toEqual({ source: "env", name: "MY_API_TOKEN" });
  });
});
