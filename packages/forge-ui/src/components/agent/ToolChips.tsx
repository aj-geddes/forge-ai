import { cn } from "@/lib/utils";
import type { ToolInfo } from "@/types/config";

const DEFAULT_MAX_VISIBLE = 8;
const DESCRIPTION_MAX_CHARS = 48;

function truncate(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;
  return `${text.slice(0, maxLength).trimEnd()}…`;
}

interface ToolChipsProps {
  tools: ToolInfo[];
  /** Maximum chips rendered before collapsing the rest into "+N more". */
  max?: number;
  className?: string;
}

/**
 * A row of pill-shaped tool chips (name + optional truncated description),
 * shared by the Dashboard's agent roster and the Chat/MiniChat agent
 * selector's scoped-capabilities preview.
 */
export function ToolChips({ tools, max = DEFAULT_MAX_VISIBLE, className }: ToolChipsProps) {
  const visible = tools.slice(0, max);
  const overflow = tools.length - visible.length;

  return (
    <div className={cn("flex flex-wrap gap-2", className)}>
      {visible.map((tool) => (
        <span
          key={tool.name}
          title={tool.description}
          className="inline-flex items-center gap-1.5 rounded-full border bg-muted/50 px-3 py-1 text-xs"
        >
          <span className="font-mono font-medium">{tool.name}</span>
          {tool.description && (
            <span className="hidden text-muted-foreground sm:inline">
              &mdash; {truncate(tool.description, DESCRIPTION_MAX_CHARS)}
            </span>
          )}
        </span>
      ))}
      {overflow > 0 && (
        <span className="inline-flex items-center rounded-full border border-dashed px-3 py-1 text-xs text-muted-foreground">
          +{overflow} more
        </span>
      )}
    </div>
  );
}
