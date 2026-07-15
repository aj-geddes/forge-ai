// Drives the real app (Vite dev server) in a real browser, mocking every
// backend call via Playwright route interception -- there is no live
// gateway in this workstream. Exercises the layered base+overlay config
// honesty contract end-to-end: edit -> save (persisted but drift_from_git)
// -> the standing drift banner appears -> Review & promote -> the
// promotion diff view renders the real unified diff and PR body.

import { test, expect } from "@playwright/test";

test("config promotion flow with mocked backend", async ({ page }) => {
  // Stateful mock state for /v1/admin/config
  let currentRev = 1;
  let driftFromGit = false;

  // 1. GET **/v1/auth/me
  await page.route("**/v1/auth/me", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          kind: "user",
          sub: "user-1",
          email: "ageddes75@gmail.com",
          name: "AJ Geddes",
          groups: [],
          roles: [],
          permissions: ["*"],
        }),
      });
    } else {
      await route.continue();
    }
  });

  // 2. GET **/health/ready
  await page.route("**/health/ready", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
    } else {
      await route.continue();
    }
  });

  // 3. GET/PUT **/v1/admin/config
  await page.route("**/v1/admin/config", async (route) => {
    const method = route.request().method();
    if (method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          config: {
            metadata: { name: "forge-agent", version: "1.0.0", description: "A test agent" },
            llm: { default_model: "gpt-4o", temperature: 0.7 },
            tools: { openapi_sources: [], manual_tools: [], workflows: [] },
            security: { agentweave: { enabled: false }, allowed_origins: [] },
            agents: { agents: [], peers: [] },
          },
          path: "forge.yaml",
          rev: currentRev,
          base_rev: "abcdef0123456789",
          drift_from_git: driftFromGit,
          source_layers: ["base"],
          mutation_policy: "overlay",
        }),
      });
    } else if (method === "PUT") {
      currentRev += 1;
      driftFromGit = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          persisted: true,
          durable: true,
          drift_from_git: true,
          rev: currentRev,
          base_rev: "abcdef0123456789",
          promotion_available: true,
          message: "Configuration saved.",
        }),
      });
    } else {
      // Fallback for unexpected methods
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          config: {
            metadata: { name: "forge-agent", version: "1.0.0", description: "A test agent" },
            llm: { default_model: "gpt-4o", temperature: 0.7 },
            tools: { openapi_sources: [], manual_tools: [], workflows: [] },
            security: { agentweave: { enabled: false }, allowed_origins: [] },
            agents: { agents: [], peers: [] },
          },
          path: "forge.yaml",
          rev: currentRev,
          base_rev: "abcdef0123456789",
          drift_from_git: driftFromGit,
          source_layers: ["base"],
          mutation_policy: "overlay",
        }),
      });
    }
  });

  // 4. GET **/v1/admin/config/promotion/diff
  await page.route("**/v1/admin/config/promotion/diff", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          diff: "@@ -1,3 +1,3 @@\n metadata:\n-  name: forge-agent\n+  name: forge-agent-renamed\n",
          pr_body: "## Summary\nPromotes the current overlay into git.\n",
          audit_trail: [
            {
              rev: 2,
              updated_by: "user-1",
              updated_at: "2026-01-01T00:00:00Z",
              summary: "Renamed agent",
            },
          ],
          rev: 2,
          base_rev: "abcdef0123456789",
          drift_from_git: true,
          changed_count: 1,
        }),
      });
    } else {
      await route.continue();
    }
  });

  // 5. GET **/v1/admin/config/schema
  await page.route("**/v1/admin/config/schema", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    } else {
      await route.continue();
    }
  });

  // 6. GET **/v1/admin/tools
  await page.route("**/v1/admin/tools", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    } else {
      await route.continue();
    }
  });

  // 7. GET **/v1/admin/peers
  await page.route("**/v1/admin/peers", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    } else {
      await route.continue();
    }
  });

  // 8. GET **/v1/admin/sessions
  await page.route("**/v1/admin/sessions", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    } else {
      await route.continue();
    }
  });

  // 9. GET **/v1/admin/activity**
  await page.route("**/v1/admin/activity**", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ activity: [] }),
      });
    } else {
      await route.continue();
    }
  });

  // 10. GET **/v1/admin/approvals
  await page.route("**/v1/admin/approvals", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([]),
      });
    } else {
      await route.continue();
    }
  });

  // Test steps
  await page.goto("/config");

  await expect(page.getByRole("heading", { name: "Config Builder" })).toBeVisible();

  await expect(page.getByRole("button", { name: "Save" })).toBeDisabled();

  const nameInput = page.getByLabel("Agent Name");
  await nameInput.fill("forge-agent-renamed");

  await expect(page.getByText("Unsaved changes")).toBeVisible();

  await expect(page.getByRole("button", { name: "Save" })).toBeEnabled();
  await page.getByRole("button", { name: "Save" }).click();

  await expect(
    page.getByText(
      "Saved — durable on this instance. Not yet in Git; Promote to make it permanent."
    )
  ).toBeVisible();

  await expect(page.getByText(/runtime change.*are live but not in Git/i)).toBeVisible();

  await page.getByRole("link", { name: /review & promote/i }).click();
  await expect(page).toHaveURL(/tab=promote/);

  await expect(page.getByRole("heading", { name: "Promotion Diff" })).toBeVisible();
  await expect(page.getByText("name: forge-agent-renamed")).toBeVisible();
  await expect(page.getByText("PR Description")).toBeVisible();
  await expect(page.getByText(/Promotes the current overlay into git/)).toBeVisible();

  await expect(page.getByText(/rev 2.*base abcdef012345/s)).toBeVisible();
});