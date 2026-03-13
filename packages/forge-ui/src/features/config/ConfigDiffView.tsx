import { useEffect, useRef, useMemo } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers } from "@codemirror/view";
import {
  syntaxHighlighting,
  defaultHighlightStyle,
  bracketMatching,
} from "@codemirror/language";
import { yaml } from "@codemirror/lang-yaml";
import { stringify } from "yaml";
import { useConfigStore } from "@/stores/configStore";
import { useUIStore } from "@/stores/uiStore";

// Shared read-only dark theme
const darkTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "oklch(0.205 0 0)",
      color: "oklch(0.985 0 0)",
    },
    ".cm-gutters": {
      backgroundColor: "oklch(0.17 0 0)",
      color: "oklch(0.5 0 0)",
      borderRight: "1px solid oklch(0.3 0 0)",
    },
    ".cm-activeLine": {
      backgroundColor: "transparent",
    },
    ".cm-activeLineGutter": {
      backgroundColor: "transparent",
    },
  },
  { dark: true },
);

const lightTheme = EditorView.theme({
  "&": {
    backgroundColor: "oklch(1 0 0)",
    color: "oklch(0.145 0 0)",
  },
  ".cm-gutters": {
    backgroundColor: "oklch(0.97 0 0)",
    color: "oklch(0.556 0 0)",
    borderRight: "1px solid oklch(0.922 0 0)",
  },
  ".cm-activeLine": {
    backgroundColor: "transparent",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
  },
});

function toYaml(config: unknown): string {
  try {
    return stringify(config, { indent: 2, lineWidth: 100 });
  } catch {
    return "";
  }
}

interface DiffLine {
  type: "unchanged" | "added" | "removed";
  text: string;
}

function computeDiffLines(original: string, proposed: string): DiffLine[] {
  const origLines = original.split("\n");
  const propLines = proposed.split("\n");
  const maxLen = Math.max(origLines.length, propLines.length);
  const result: DiffLine[] = [];

  for (let i = 0; i < maxLen; i++) {
    const origLine = origLines[i];
    const propLine = propLines[i];

    if (origLine === propLine) {
      result.push({ type: "unchanged", text: propLine ?? "" });
    } else if (origLine === undefined) {
      result.push({ type: "added", text: propLine ?? "" });
    } else if (propLine === undefined) {
      result.push({ type: "removed", text: origLine });
    } else {
      result.push({ type: "removed", text: origLine });
      result.push({ type: "added", text: propLine });
    }
  }

  return result;
}

function ReadOnlyEditor({
  content,
  darkMode,
}: {
  content: string;
  darkMode: boolean;
}) {
  const editorRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!editorRef.current) return;

    const state = EditorState.create({
      doc: content,
      extensions: [
        lineNumbers(),
        bracketMatching(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        yaml(),
        darkMode ? darkTheme : lightTheme,
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
        EditorView.lineWrapping,
      ],
    });

    const view = new EditorView({
      state,
      parent: editorRef.current,
    });

    viewRef.current = view;

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [content, darkMode]);

  return (
    <div
      ref={editorRef}
      className="min-h-[400px] overflow-hidden rounded-md border border-input"
    />
  );
}

export function ConfigDiffView() {
  const { original, draft, isDirty } = useConfigStore();
  const darkMode = useUIStore((s) => s.darkMode);

  const originalYaml = useMemo(
    () => (original ? toYaml(original) : ""),
    [original],
  );
  const proposedYaml = useMemo(
    () => (draft ? toYaml(draft) : ""),
    [draft],
  );

  const diffLines = useMemo(
    () => computeDiffLines(originalYaml, proposedYaml),
    [originalYaml, proposedYaml],
  );

  const hasChanges = diffLines.some((l) => l.type !== "unchanged");

  if (!original || !draft) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        No configuration loaded
      </div>
    );
  }

  if (!isDirty && !hasChanges) {
    return (
      <div className="flex items-center justify-center py-12 text-muted-foreground">
        No changes to display. Edit the configuration in the Visual or YAML tab.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary of changes */}
      <div className="flex gap-4 text-sm">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-red-500/20 border border-red-500/40" />
          Removed
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded-sm bg-green-500/20 border border-green-500/40" />
          Added
        </span>
      </div>

      {/* Inline diff view */}
      <div className="overflow-hidden rounded-md border border-input">
        <div className="overflow-auto font-mono text-sm">
          {diffLines.map((line, idx) => (
            <div
              key={idx}
              className={
                line.type === "added"
                  ? "bg-green-500/10 text-green-700 dark:text-green-400"
                  : line.type === "removed"
                    ? "bg-red-500/10 text-red-700 dark:text-red-400"
                    : ""
              }
            >
              <span className="inline-block w-8 select-none text-right text-muted-foreground pr-2">
                {line.type === "added"
                  ? "+"
                  : line.type === "removed"
                    ? "-"
                    : " "}
              </span>
              <span className="whitespace-pre">{line.text}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Side-by-side editors */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-medium text-muted-foreground">
            Current (Saved)
          </h3>
          <ReadOnlyEditor content={originalYaml} darkMode={darkMode} />
        </div>
        <div>
          <h3 className="mb-2 text-sm font-medium text-muted-foreground">
            Proposed (Draft)
          </h3>
          <ReadOnlyEditor content={proposedYaml} darkMode={darkMode} />
        </div>
      </div>
    </div>
  );
}
