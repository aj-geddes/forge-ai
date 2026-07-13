import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("has no API-key input -- BFF mode holds no client-side credential", () => {
    render(<LoginPage />);

    expect(screen.queryByLabelText(/api.?key/i)).not.toBeInTheDocument();
    expect(
      document.querySelector('input[type="password"]'),
    ).not.toBeInTheDocument();
    expect(document.querySelector("form")).not.toBeInTheDocument();
  });

  it("renders an honest 'Sign in with GitHub' button, not a fake form", () => {
    render(<LoginPage />);

    expect(
      screen.getByRole("button", { name: /sign in with github/i }),
    ).toBeInTheDocument();
  });

  it("navigates (full browser navigation) to /auth/login when clicked", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/config", search: "?tab=yaml", assign },
    });

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in with github/i }));

    expect(assign).toHaveBeenCalledWith(
      "/auth/login?next=" + encodeURIComponent("/config?tab=yaml"),
    );
  });

  it("omits the next param at the app root, where there is nothing to return to", async () => {
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/", search: "", assign },
    });

    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole("button", { name: /sign in with github/i }));

    expect(assign).toHaveBeenCalledWith("/auth/login");
  });
});
