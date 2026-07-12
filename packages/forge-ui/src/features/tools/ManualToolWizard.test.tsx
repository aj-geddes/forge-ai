import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ManualToolWizard } from "./ManualToolWizard";
import { useToolStore } from "@/stores/toolStore";

function renderWizard() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ManualToolWizard />
    </QueryClientProvider>,
  );
}

describe("ManualToolWizard basic auth payload", () => {
  beforeEach(() => {
    window.HTMLDialogElement.prototype.showModal = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = true;
    });
    window.HTMLDialogElement.prototype.close = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = false;
    });

    useToolStore.setState({
      wizardType: "manual",
      step: 6,
      manualData: {
        name: "get_weather",
        description: "Gets the weather",
        baseUrl: "https://api.example.com",
        endpoint: "/weather",
        method: "GET",
        auth: {
          type: "basic",
          token: "",
          headerName: "X-API-Key",
          username: "API_USERNAME",
          password: "API_PASSWORD",
        },
        parameters: [],
        responseMapping: { resultPath: "", fieldMap: [] },
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("emits SecretRef ({source:'env', name}) for both username and password, never raw values", async () => {
    // AuthConfig.username / .password on the backend (forge_config/schema.py) are
    // `SecretRef | None`, not plain strings.
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

    renderWizard();

    await userEvent.click(screen.getByRole("button", { name: "Add Tool" }));

    await vi.waitFor(() => expect(putBodies.length).toBeGreaterThan(0));

    const body = putBodies[0] as {
      config: {
        tools: {
          manual_tools: Array<{ api: { auth?: { username?: unknown; password?: unknown } } }>;
        };
      };
    };
    const auth = body.config.tools.manual_tools[0]?.api.auth;
    expect(auth?.username).toEqual({ source: "env", name: "API_USERNAME" });
    expect(auth?.password).toEqual({ source: "env", name: "API_PASSWORD" });
  });
});
