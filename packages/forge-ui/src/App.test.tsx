import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";

function renderApp(initialEntries: string[] = ["/"]) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function mockPrincipal(permissions: string[]) {
  return {
    kind: "user",
    sub: "user-1",
    email: "ageddes75@gmail.com",
    name: "AJ Geddes",
    groups: [],
    roles: [],
    permissions,
  };
}

function stubFetchWithAuth(permissions: string[] | null) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string) => {
      if (url === "/v1/auth/me") {
        if (permissions === null) {
          return Promise.resolve({
            ok: false,
            status: 401,
            statusText: "Unauthorized",
            json: async () => ({ error: "missing_credentials" }),
          });
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => mockPrincipal(permissions),
        });
      }
      if (url === "/health/ready") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ status: "ready", version: "0.1.0", components: {} }),
        });
      }
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => ({}),
      });
    }),
  );
}

describe("App -- route protection", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the login page (not app routes) when GET /v1/auth/me is 401", async () => {
    stubFetchWithAuth(null);

    renderApp(["/"]);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /sign in with github/i }),
      ).toBeInTheDocument(),
    );
  });

  it("does not perform a full navigation to /auth/login just for landing unauthenticated in the SPA", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/", search: "", assign },
    });
    stubFetchWithAuth(null);

    renderApp(["/"]);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /sign in with github/i }),
      ).toBeInTheDocument(),
    );
    expect(assign).not.toHaveBeenCalled();
  });

  it("renders the requested app route once authenticated with the needed permission", async () => {
    stubFetchWithAuth(["config:read", "agent:invoke"]);

    renderApp(["/guide"]);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /sign in with github/i })).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Guide" })).toBeInTheDocument(),
    );
  });

  it("renders the Approvals route for an authenticated user with config:read", async () => {
    stubFetchWithAuth(["config:read"]);

    renderApp(["/approvals"]);

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /sign in with github/i })).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /approvals/i })).toBeInTheDocument(),
    );
  });

  it("shows a permission-denied state (not a login redirect) for an authenticated user missing the route's permission", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/config", search: "", assign },
    });
    stubFetchWithAuth(["agent:invoke"]); // authenticated, but no config:read

    renderApp(["/config"]);

    await waitFor(() =>
      expect(screen.getByText(/don.t have permission/i)).toBeInTheDocument(),
    );
    expect(assign).not.toHaveBeenCalled();
  });
});
