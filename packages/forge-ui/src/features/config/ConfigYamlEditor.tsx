import { useEffect, useRef, useCallback, useState } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, lineNumbers, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import {
  syntaxHighlighting,
  defaultHighlightStyle,
  bracketMatching,
} from "@codemirror/language";
import { yaml } from "@codemirror/lang-yaml";
import { parse, stringify } from "yaml";
import { useConfigStore } from "@/stores/configStore";
import { useUIStore } from "@/stores/uiStore";
import type { ForgeConfig } from "@/types/config";

// Dark theme for CodeMirror
const darkTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "oklch(0.205 0 0)",
      color: "oklch(0.985 0 0)",
    },
    ".cm-content": {
      caretColor: "oklch(0.708 0.165 254.624)",
    },
    ".cm-cursor, .cm-dropCursor": {
      borderLeftColor: "oklch(0.708 0.165 254.624)",
    },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection":
      {
        backgroundColor: "oklch(0.3 0.05 254.624 / 0.4)",
      },
    ".cm-gutters": {
      backgroundColor: "oklch(0.17 0 0)",
      color: "oklch(0.5 0 0)",
      borderRight: "1px solid oklch(0.3 0 0)",
    },
    ".cm-activeLineGutter": {
      backgroundColor: "oklch(0.25 0 0)",
    },
    ".cm-activeLine": {
      backgroundColor: "oklch(0.22 0 0)",
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
  ".cm-activeLineGutter": {
    backgroundColor: "oklch(0.95 0 0)",
  },
  ".cm-activeLine": {
    backgroundColor: "oklch(0.97 0 0)",
  },
});

export function ConfigYamlEditor() {
  const editorRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const isInternalUpdate = useRef(false);
  const [parseError, setParseError] = useState<string | null>(null);

  const { draft, updateDraft } = useConfigStore();
  const darkMode = useUIStore((s) => s.darkMode);

  // Convert config to YAML string
  const configToYaml = useCallback((config: ForgeConfig): string => {
    try {
      return stringify(config, { indent: 2, lineWidth: 100 });
    } catch {
      return "";
    }
  }, []);

  // Handle editor content changes
  const handleDocChange = useCallback(
    (doc: string) => {
      if (isInternalUpdate.current) return;
      try {
        const parsed = parse(doc) as ForgeConfig;
        if (
          parsed &&
          typeof parsed === "object" &&
          parsed.metadata &&
          parsed.llm
        ) {
          setParseError(null);
          updateDraft(parsed);
        }
      } catch (e) {
        setParseError(
          e instanceof Error ? e.message : "Invalid YAML syntax",
        );
      }
    },
    [updateDraft],
  );

  // Initialize CodeMirror
  useEffect(() => {
    if (!editorRef.current) return;

    const initialDoc = draft ? configToYaml(draft) : "";

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        handleDocChange(update.state.doc.toString());
      }
    });

    const state = EditorState.create({
      doc: initialDoc,
      extensions: [
        lineNumbers(),
        history(),
        bracketMatching(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        yaml(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        darkMode ? darkTheme : lightTheme,
        updateListener,
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
    // Only re-create on dark mode change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [darkMode]);

  // Sync external draft changes into the editor
  useEffect(() => {
    const view = viewRef.current;
    if (!view || !draft) return;

    const newYaml = configToYaml(draft);
    const currentDoc = view.state.doc.toString();

    if (newYaml !== currentDoc) {
      isInternalUpdate.current = true;
      view.dispatch({
        changes: {
          from: 0,
          to: view.state.doc.length,
          insert: newYaml,
        },
      });
      isInternalUpdate.current = false;
    }
  }, [draft, configToYaml]);

  return (
    <div className="flex flex-col gap-2">
      {parseError && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {parseError}
        </div>
      )}
      <div
        ref={editorRef}
        className="min-h-[500px] overflow-hidden rounded-md border border-input"
      />
    </div>
  );
}
