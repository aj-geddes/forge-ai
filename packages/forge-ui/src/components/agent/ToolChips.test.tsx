import { describe, it, expect, afterEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ToolChips } from "./ToolChips";
import type { ToolInfo } from "@/types/config";

afterEach(() => {
  cleanup();
});

describe("ToolChips", () => {
  it("renders a chip for each tool", () => {
    const tools: ToolInfo[] = [
      { name: "get_weather", description: "Gets current weather" },
      { name: "get_crypto_price", description: "Gets the crypto price" },
    ];
    render(<ToolChips tools={tools} />);

    expect(screen.getByText("get_weather")).toBeInTheDocument();
    expect(screen.getByText("get_crypto_price")).toBeInTheDocument();
  });

  it("caps visible chips at `max` and shows an overflow indicator", () => {
    const tools: ToolInfo[] = Array.from({ length: 5 }, (_, i) => ({
      name: `tool_${i}`,
      description: "",
    }));
    render(<ToolChips tools={tools} max={3} />);

    expect(screen.getByText("tool_0")).toBeInTheDocument();
    expect(screen.getByText("tool_2")).toBeInTheDocument();
    expect(screen.queryByText("tool_3")).not.toBeInTheDocument();
    expect(screen.getByText("+2 more")).toBeInTheDocument();
  });

  it("renders nothing extra when there is no overflow", () => {
    render(<ToolChips tools={[{ name: "solo", description: "" }]} />);
    expect(screen.queryByText(/more$/)).not.toBeInTheDocument();
  });
});
