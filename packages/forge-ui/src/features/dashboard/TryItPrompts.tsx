import { useNavigate } from "react-router-dom";
import { MousePointerClick } from "lucide-react";
import { useTools } from "@/api/hooks";
import { useChatStore } from "@/stores/chatStore";
import { Eyebrow, HelpText, SkeletonLine } from "./shared";

const MAX_TRY_IT_PROMPTS = 6;

const FALLBACK_PROMPTS = [
  "What can you help me with?",
  "What tools do you have access to?",
  "Tell me something interesting.",
  "What's the most useful thing you can do?",
] as const;

interface PromptCategory {
  name: string;
  keywords: string[];
  prompt: string;
}

const PROMPT_CATEGORIES: PromptCategory[] = [
  { name: "weather", keywords: ["weather"], prompt: "What's the weather in Tokyo right now?" },
  {
    name: "crypto",
    keywords: ["crypto", "coin", "bitcoin"],
    prompt: "What's the current price of Bitcoin?",
  },
  {
    name: "define",
    keywords: ["define", "dictionary"],
    prompt: "Define the word 'serendipity'.",
  },
  { name: "github", keywords: ["github"], prompt: "Find popular TypeScript repositories on GitHub." },
  {
    name: "exchange",
    keywords: ["exchange", "currency", "fx"],
    prompt: "Convert 100 USD to EUR.",
  },
];

interface ToolLike {
  name: string;
  description: string;
}

/**
 * Builds 4-6 example prompts tailored to the agent's real, configured tools.
 * One prompt per recognized tool category (weather, crypto, dictionary,
 * GitHub, currency exchange), then a generic "try this tool" prompt for any
 * remaining unmatched tools, then a fixed fallback if nothing matched at all
 * (or no tools are configured).
 */
export function buildTryItPrompts(tools: ToolLike[]): string[] {
  const matchedCategories = new Set<string>();
  const consumedToolIndexes = new Set<number>();

  for (const category of PROMPT_CATEGORIES) {
    for (let i = 0; i < tools.length; i++) {
      const tool = tools[i]!;
      const haystack = `${tool.name} ${tool.description}`.toLowerCase();
      if (category.keywords.some((keyword) => haystack.includes(keyword))) {
        matchedCategories.add(category.name);
        // Consume every tool that matches this category (not just the
        // first) so a second "weather" tool doesn't also get a redundant
        // generic "Try the X tool" prompt below.
        consumedToolIndexes.add(i);
      }
    }
  }

  const prompts: string[] = PROMPT_CATEGORIES.filter((c) => matchedCategories.has(c.name)).map(
    (c) => c.prompt,
  );

  for (let i = 0; i < tools.length && prompts.length < MAX_TRY_IT_PROMPTS; i++) {
    if (!consumedToolIndexes.has(i)) {
      prompts.push(`Try the ${tools[i]!.name} tool.`);
    }
  }

  const result = prompts.slice(0, MAX_TRY_IT_PROMPTS);
  return result.length > 0 ? result : [...FALLBACK_PROMPTS];
}

function TryItSkeleton() {
  return (
    <div className="flex flex-wrap gap-2">
      {Array.from({ length: 4 }).map((_, i) => (
        <SkeletonLine key={i} className="h-8 w-40 rounded-full" />
      ))}
    </div>
  );
}

/**
 * Clicking a chip queues the prompt on chatStore and navigates to Chat,
 * where it's dropped into a fresh (or existing) session's input for the
 * user to review and send -- see chatStore.pendingPrompt and ChatPage.
 */
export function TryItPrompts() {
  const navigate = useNavigate();
  const setPendingPrompt = useChatStore((s) => s.setPendingPrompt);
  const { data: tools, isLoading } = useTools();

  const prompts = buildTryItPrompts(tools ?? []);

  const handleSelect = (prompt: string) => {
    setPendingPrompt(prompt);
    navigate("/chat");
  };

  return (
    <section className="space-y-2">
      <Eyebrow icon={MousePointerClick}>Try it</Eyebrow>
      <HelpText>Pick a prompt to see the agent in action -- it opens in Chat, ready to send.</HelpText>
      {isLoading ? (
        <TryItSkeleton />
      ) : (
        <div className="flex flex-wrap gap-2">
          {prompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => handleSelect(prompt)}
              className="rounded-full border bg-card px-4 py-2 text-sm transition-colors hover:border-primary/40 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
