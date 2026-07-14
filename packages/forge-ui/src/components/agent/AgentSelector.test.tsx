import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AgentSelector } from "./AgentSelector";
import type { AgentDef, ToolInfo } from "@/types/config";

afterEach(() => {
  cleanup();
});

const agents: AgentDef[] = [
  { name: "assistant", description: "General helper", tools: [] },
  { name: "researcher", description: "Digs up facts", tools: ["search_web"] },
];

const tools: ToolInfo[] = [
  { name: "search_web", description: "Searches the web" },
  { name: "get_weather", description: "Gets current weather" },
];

describe("AgentSelector", () => {
  it("renders nothing when there are no agents to choose from", () => {
    const { container } = render(
      <AgentSelector agents={[]} value="" onChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("lists every agent as a selectable option", () => {
    render(<AgentSelector agents={agents} value="assistant" onChange={vi.fn()} />);

    const select = screen.getByRole("combobox");
    expect(select).toHaveValue("assistant");
    expect(screen.getByRole("option", { name: /assistant/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /researcher/i })).toBeInTheDocument();
  });

  it("calls onChange with the newly picked agent's name", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<AgentSelector agents={agents} value="assistant" onChange={onChange} />);

    await user.selectOptions(screen.getByRole("combobox"), "researcher");

    expect(onChange).toHaveBeenCalledWith("researcher");
  });

  it("shows the selected agent's scoped capabilities, resolved to human labels", () => {
    render(
      <AgentSelector agents={agents} value="researcher" onChange={vi.fn()} tools={tools} />,
    );

    expect(screen.getByText("search_web")).toBeInTheDocument();
    expect(screen.queryByText("get_weather")).not.toBeInTheDocument();
  });

  it("labels an agent with an empty tools[] as having full (unscoped) access", () => {
    render(
      <AgentSelector agents={agents} value="assistant" onChange={vi.fn()} tools={tools} />,
    );

    expect(screen.getByText(/all tools/i)).toBeInTheDocument();
  });
});
