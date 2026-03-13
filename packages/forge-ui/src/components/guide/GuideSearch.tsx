import { useMemo } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { allSections, type GuideSection } from "@/content/index";

interface GuideSearchProps {
  query: string;
  onQueryChange: (query: string) => void;
  onSelectSection: (sectionId: string) => void;
  showResults?: boolean;
}

export interface SearchResult {
  section: GuideSection;
  matchField: string;
  matchText: string;
}

function highlightMatch(text: string, query: string): string {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return text.replace(
    new RegExp(`(${escaped})`, "gi"),
    "**$1**",
  );
}

export function searchSections(query: string): SearchResult[] {
  const q = query.toLowerCase().trim();
  if (!q) return [];

  const results: SearchResult[] = [];

  for (const section of allSections) {
    if (section.title.toLowerCase().includes(q)) {
      results.push({
        section,
        matchField: "title",
        matchText: highlightMatch(section.title, q),
      });
      continue;
    }

    if (section.overview.toLowerCase().includes(q)) {
      results.push({
        section,
        matchField: "overview",
        matchText: highlightMatch(
          section.overview.slice(0, 120) + (section.overview.length > 120 ? "..." : ""),
          q,
        ),
      });
      continue;
    }

    const matchedConcept = section.concepts.find(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q),
    );
    if (matchedConcept) {
      results.push({
        section,
        matchField: "concept",
        matchText: highlightMatch(matchedConcept.title, q),
      });
      continue;
    }

    const matchedStep = section.steps.find(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.content.toLowerCase().includes(q),
    );
    if (matchedStep) {
      results.push({
        section,
        matchField: "step",
        matchText: highlightMatch(matchedStep.title, q),
      });
      continue;
    }

    if (section.troubleshooting) {
      const matchedFaq = section.troubleshooting.find(
        (f) =>
          f.question.toLowerCase().includes(q) ||
          f.answer.toLowerCase().includes(q),
      );
      if (matchedFaq) {
        results.push({
          section,
          matchField: "faq",
          matchText: highlightMatch(matchedFaq.question, q),
        });
      }
    }
  }

  return results;
}

export function filterSections(query: string): GuideSection[] {
  const q = query.toLowerCase().trim();
  if (!q) return allSections;

  return allSections.filter((section) => {
    if (section.title.toLowerCase().includes(q)) return true;
    if (section.overview.toLowerCase().includes(q)) return true;
    if (section.concepts.some(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q),
    )) return true;
    if (section.steps.some(
      (s) =>
        s.title.toLowerCase().includes(q) ||
        s.content.toLowerCase().includes(q),
    )) return true;
    if (section.troubleshooting?.some(
      (f) =>
        f.question.toLowerCase().includes(q) ||
        f.answer.toLowerCase().includes(q),
    )) return true;
    return false;
  });
}

export function GuideSearch({
  query,
  onQueryChange,
  onSelectSection,
  showResults = true,
}: GuideSearchProps) {
  const results = useMemo(() => searchSections(query), [query]);

  return (
    <div className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          placeholder="Search guide..."
          className="pl-9"
        />
      </div>
      {showResults && query.trim() && results.length > 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-card shadow-lg">
          {results.map((result) => (
            <button
              key={result.section.id}
              onClick={() => {
                onSelectSection(result.section.id);
                onQueryChange("");
              }}
              className="flex w-full flex-col gap-0.5 px-3 py-2 text-left text-sm hover:bg-accent"
            >
              <span className="font-medium">{result.section.title}</span>
              <span className="text-xs text-muted-foreground">
                {result.matchField === "title"
                  ? result.section.overview.slice(0, 80) + "..."
                  : result.matchText.replace(/\*\*/g, "")}
              </span>
            </button>
          ))}
        </div>
      )}
      {showResults && query.trim() && results.length === 0 && (
        <div className="absolute z-50 mt-1 w-full rounded-md border bg-card px-3 py-2 shadow-lg">
          <p className="text-sm text-muted-foreground">No results found</p>
        </div>
      )}
    </div>
  );
}
