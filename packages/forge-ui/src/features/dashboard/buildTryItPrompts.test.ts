import { describe, it, expect } from "vitest";
import { buildTryItPrompts } from "./TryItPrompts";

describe("buildTryItPrompts", () => {
  it("returns the fixed fallback prompts when there are no tools", () => {
    expect(buildTryItPrompts([])).toEqual([
      "What can you help me with?",
      "What tools do you have access to?",
      "Tell me something interesting.",
      "What's the most useful thing you can do?",
    ]);
  });

  it("returns one prompt per matched category, never duplicating a category matched by multiple tools", () => {
    const prompts = buildTryItPrompts([
      { name: "get_weather", description: "Current weather for a city" },
      { name: "forecast_weather", description: "7-day weather forecast" },
    ]);

    expect(prompts).toEqual(["What's the weather in Tokyo right now?"]);
  });

  it("adds a generic prompt for tools that don't match any known category", () => {
    const prompts = buildTryItPrompts([{ name: "roll_dice", description: "Rolls a die" }]);
    expect(prompts).toEqual(["Try the roll_dice tool."]);
  });

  it("caps the result at 6 prompts even with many unmatched tools", () => {
    const tools = Array.from({ length: 10 }, (_, i) => ({
      name: `tool_${i}`,
      description: "does something",
    }));
    const prompts = buildTryItPrompts(tools);
    expect(prompts).toHaveLength(6);
  });
});
