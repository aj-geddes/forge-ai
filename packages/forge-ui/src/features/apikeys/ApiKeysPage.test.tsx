import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiKeysPage } from "./ApiKeysPage";

const ME_RESPONSE = {
  kind: "user",
  sub: "u-1",
  email: "ageddes75@gmail.com",
  name: "AJ Geddes",
  groups: [],
  roles: ["user", "viewer"],
  permissions: ["config:read", "agent:invoke"],
};

function renderApiKeysPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ApiKeysPage />
    </QueryClientProvider>,
  );
}

/** Routes a stubbed fetch by method + path suffix, matching this test file's needs. */
function stubFetch(handlers: {
  me?: () => unknown;
  listTokens?: () => unknown;
  createToken?: (body: Record<string, unknown>) => { status: number; body: unknown };
  deleteToken?: (id: string) => { status: number };
}) {
  const fetchMock = vi.fn().mockImplementation((url: string, options?: RequestInit) => {
    const method = (options?.method ?? "GET").toUpperCase();

    if (url === "/v1/auth/me") {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => (handlers.me ? handlers.me() : ME_RESPONSE),
      });
    }

    if (url === "/v1/auth/tokens" && method === "GET") {
      const body = handlers.listTokens ? handlers.listTokens() : { tokens: [] };
      return Promise.resolve({ ok: true, status: 200, json: async () => body });
    }

    if (url === "/v1/auth/tokens" && method === "POST") {
      const parsedBody = JSON.parse(options?.body as string) as Record<string, unknown>;
      const { status, body } = handlers.createToken
        ? handlers.createToken(parsedBody)
        : { status: 201, body: {} };
      return Promise.resolve({
        ok: status >= 200 && status < 300,
        status,
        json: async () => body,
      });
    }

    if (url.startsWith("/v1/auth/tokens/") && method === "DELETE") {
      const id = url.replace("/v1/auth/tokens/", "");
      const { status } = handlers.deleteToken
        ? handlers.deleteToken(id)
        : { status: 204 };
      return Promise.resolve({ ok: status < 300, status, json: async () => undefined });
    }

    throw new Error(`Unhandled fetch: ${method} ${url}`);
  });

  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("ApiKeysPage", () => {
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
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("renders the user's API keys from GET /v1/auth/tokens (label, roles, created, expires, revoked state)", async () => {
    stubFetch({
      listTokens: () => ({
        tokens: [
          {
            id: "u_1",
            label: "laptop CLI",
            roles: ["user"],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2099-01-01T00:00:00Z",
            revoked_at: null,
          },
          {
            id: "u_2",
            label: "old ci key",
            roles: ["viewer"],
            created_at: "2025-01-01T00:00:00Z",
            expires_at: "2025-06-01T00:00:00Z",
            revoked_at: "2025-05-01T00:00:00Z",
          },
        ],
      }),
    });

    renderApiKeysPage();

    await waitFor(() => expect(screen.getByText("laptop CLI")).toBeInTheDocument());
    expect(screen.getByText("old ci key")).toBeInTheDocument();

    const activeRow = screen.getByText("laptop CLI").closest("tr");
    expect(activeRow).not.toBeNull();
    expect(within(activeRow as HTMLElement).getByText("user")).toBeInTheDocument();
    expect(within(activeRow as HTMLElement).getByText("Active")).toBeInTheDocument();

    const revokedRow = screen.getByText("old ci key").closest("tr");
    expect(revokedRow).not.toBeNull();
    expect(within(revokedRow as HTMLElement).getByText("viewer")).toBeInTheDocument();
    expect(within(revokedRow as HTMLElement).getByText("Revoked")).toBeInTheDocument();
  });

  it("creates a key: POSTs {label, roles, ttl_seconds} and shows the raw token exactly once in a dismissible dialog", async () => {
    let tokensAfterCreate: unknown[] = [];
    const fetchMock = stubFetch({
      listTokens: () => ({ tokens: tokensAfterCreate }),
      createToken: (body) => {
        expect(body).toEqual({
          label: "laptop CLI",
          roles: ["viewer"],
          ttl_seconds: 2_592_000,
        });
        tokensAfterCreate = [
          {
            id: "u_9",
            label: "laptop CLI",
            roles: ["viewer"],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2026-01-31T00:00:00Z",
            revoked_at: null,
          },
        ];
        return {
          status: 201,
          body: {
            id: "u_9",
            token: "forge_sk_u_9_supersecretvalue",
            label: "laptop CLI",
            roles: ["viewer"],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2026-01-31T00:00:00Z",
          },
        };
      },
    });

    const user = userEvent.setup();
    renderApiKeysPage();

    await waitFor(() =>
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /create api key/i }));
    await user.type(screen.getByLabelText("Label"), "laptop CLI");
    await user.click(screen.getByLabelText("viewer"));
    await user.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() =>
      expect(screen.getByText(/api key created/i)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/will not be shown again/i),
    ).toBeInTheDocument();
    expect(screen.getByTestId("minted-token-value")).toHaveTextContent(
      "forge_sk_u_9_supersecretvalue",
    );

    expect(fetchMock).toHaveBeenCalled();
  });

  it("never writes the freshly minted raw token to localStorage or sessionStorage", async () => {
    let tokensAfterCreate: unknown[] = [];
    stubFetch({
      listTokens: () => ({ tokens: tokensAfterCreate }),
      createToken: () => {
        tokensAfterCreate = [
          {
            id: "u_9",
            label: "laptop CLI",
            roles: [],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2026-01-31T00:00:00Z",
            revoked_at: null,
          },
        ];
        return {
          status: 201,
          body: {
            id: "u_9",
            token: "forge_sk_u_9_supersecretvalue",
            label: "laptop CLI",
            roles: [],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2026-01-31T00:00:00Z",
          },
        };
      },
    });

    const localSetItem = vi.spyOn(window.localStorage, "setItem");
    const sessionSetItem = vi.spyOn(window.sessionStorage, "setItem");

    const user = userEvent.setup();
    renderApiKeysPage();

    await waitFor(() =>
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /create api key/i }));
    await user.type(screen.getByLabelText("Label"), "laptop CLI");
    await user.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() =>
      expect(screen.getByTestId("minted-token-value")).toHaveTextContent(
        "forge_sk_u_9_supersecretvalue",
      ),
    );

    // Dismiss the dialog -- the ephemeral state that held the token is dropped.
    await user.click(
      screen.getByRole("button", { name: /done, i've saved my key/i }),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("minted-token-value")).not.toBeInTheDocument(),
    );

    const rawTokenWasPersisted = (spy: typeof localSetItem) =>
      spy.mock.calls.some(([, value]) =>
        String(value).includes("forge_sk_u_9_supersecretvalue"),
      );

    expect(rawTokenWasPersisted(localSetItem)).toBe(false);
    expect(rawTokenWasPersisted(sessionSetItem)).toBe(false);
  });

  it("revokes a key: DELETEs /v1/auth/tokens/{id} and refreshes the list", async () => {
    let revoked = false;
    const fetchMock = stubFetch({
      listTokens: () => ({
        tokens: [
          {
            id: "u_1",
            label: "laptop CLI",
            roles: ["user"],
            created_at: "2026-01-01T00:00:00Z",
            expires_at: "2099-01-01T00:00:00Z",
            revoked_at: revoked ? "2026-01-02T00:00:00Z" : null,
          },
        ],
      }),
      deleteToken: (id) => {
        expect(id).toBe("u_1");
        revoked = true;
        return { status: 204 };
      },
    });

    const user = userEvent.setup();
    renderApiKeysPage();

    await waitFor(() => expect(screen.getByText("laptop CLI")).toBeInTheDocument());
    expect(screen.getByText("Active")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Revoke" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(screen.getByText("Revoked")).toBeInTheDocument());

    const deleteCall = fetchMock.mock.calls.find(
      ([, options]) => (options as RequestInit | undefined)?.method === "DELETE",
    );
    expect(deleteCall?.[0]).toBe("/v1/auth/tokens/u_1");
  });

  it("surfaces the backend's {error} message inline (e.g. escalation_denied)", async () => {
    stubFetch({
      listTokens: () => ({ tokens: [] }),
      createToken: () => ({ status: 403, body: { error: "escalation_denied" } }),
    });

    const user = userEvent.setup();
    renderApiKeysPage();

    await waitFor(() =>
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /create api key/i }));
    await user.type(screen.getByLabelText("Label"), "too many roles");
    await user.click(screen.getByRole("button", { name: "Create key" }));

    await waitFor(() =>
      expect(screen.getByText("escalation_denied")).toBeInTheDocument(),
    );

    // The form dialog stays open on error -- no once-only token dialog appears.
    expect(screen.queryByTestId("minted-token-value")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Label")).toBeInTheDocument();
  });

  it("offers only the current user's own roles in the create dialog's role selector", async () => {
    stubFetch({
      me: () => ({ ...ME_RESPONSE, roles: ["user"] }),
      listTokens: () => ({ tokens: [] }),
    });

    const user = userEvent.setup();
    renderApiKeysPage();

    await waitFor(() =>
      expect(screen.getByText(/no api keys yet/i)).toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: /create api key/i }));

    expect(await screen.findByLabelText("user")).toBeInTheDocument();
    expect(screen.queryByLabelText("admin")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("viewer")).not.toBeInTheDocument();
  });

  it("hides the create/table UI and explains the feature is disabled on a 404 from GET /v1/auth/tokens", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url === "/v1/auth/me") {
          return Promise.resolve({ ok: true, status: 200, json: async () => ME_RESPONSE });
        }
        if (url === "/v1/auth/tokens") {
          return Promise.resolve({
            ok: false,
            status: 404,
            json: async () => ({ error: "not_found" }),
            statusText: "Not Found",
          });
        }
        throw new Error(`Unhandled fetch: ${url}`);
      }),
    );

    renderApiKeysPage();

    await waitFor(() =>
      expect(screen.getByText(/api keys are not enabled/i)).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: /create api key/i }),
    ).not.toBeInTheDocument();
  });
});
