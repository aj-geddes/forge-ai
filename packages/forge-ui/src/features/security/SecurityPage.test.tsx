import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SecurityPage } from "./SecurityPage";

function renderSecurityPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <SecurityPage />
    </QueryClientProvider>,
  );
}

describe("SecurityPage posture checks", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("reads the backend's actual SecurityConfig shape (forge_config/schema.py) and reports all 4 posture checks as active", async () => {
    // Backend SecurityConfig: { agentweave: {enabled, trust_policy, ...},
    // api_keys: {enabled, keys: SecretRef[]}, rate_limit_rpm: int,
    // allowed_origins: string[] } -- not the old cors_origins/rate_limit/trust_store shape.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          path: "forge.yaml",
          config: {
            metadata: { name: "forge", version: "0.1.0" },
            llm: { default_model: "gpt-4o" },
            tools: {},
            security: {
              agentweave: {
                enabled: true,
                trust_domain: "forge.local",
                trust_policy: "strict",
              },
              api_keys: {
                enabled: true,
                keys: [{ source: "env", name: "***REDACTED***" }],
              },
              rate_limit_rpm: 60,
              allowed_origins: ["https://app.example.com"],
            },
          },
        }),
      }),
    );

    renderSecurityPage();

    await waitFor(() => {
      expect(screen.getByText(/4\/4 controls active/i)).toBeInTheDocument();
    });
  });
});
