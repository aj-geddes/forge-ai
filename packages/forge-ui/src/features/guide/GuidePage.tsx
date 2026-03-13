import { useState, useMemo, useCallback, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  BookOpen,
  ExternalLink,
  ArrowRight,
  PlayCircle,
  Cpu,
  FileText,
  Wrench,
  Globe,
  Hammer,
  GitBranch,
  FlaskConical,
  MessageSquare,
  Radio,
  Network,
  ShieldCheck,
  Search,
  KeyRound,
  Fingerprint,
  Gauge,
  Key,
  Scale,
  ScrollText,
  HeartPulse,
  AlertTriangle,
  Unplug,
  Bot,
  Server,
  Blocks,
  Shield,
  Zap,
  RefreshCw,
  LayoutDashboard,
  Tag,
  Brain,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { GuideSearch, filterSections } from "@/components/guide/GuideSearch";
import { useGuideStore } from "@/stores/guideStore";
import { allSections, sectionMap, type GuideSection } from "@/content/index";
import { cn } from "@/lib/utils";

const iconMap: Record<string, LucideIcon> = {
  Cpu,
  FileText,
  Wrench,
  Globe,
  Hammer,
  GitBranch,
  FlaskConical,
  MessageSquare,
  Radio,
  Network,
  ShieldCheck,
  Search,
  KeyRound,
  Fingerprint,
  Gauge,
  Key,
  Scale,
  ScrollText,
  HeartPulse,
  AlertTriangle,
  Unplug,
  Bot,
  Server,
  Blocks,
  Shield,
  Zap,
  RefreshCw,
  LayoutDashboard,
  Tag,
  Brain,
};

function getIcon(name: string): LucideIcon {
  return iconMap[name] ?? Cpu;
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <div className="flex items-center justify-between bg-muted px-3 py-1.5">
        <span className="text-xs font-medium text-muted-foreground">
          {language}
        </span>
      </div>
      <pre className="overflow-x-auto bg-muted/30 p-4 text-sm leading-relaxed">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function SectionContent({ section }: { section: GuideSection }) {
  const navigate = useNavigate();

  return (
    <div className="space-y-8">
      {/* Title & Overview */}
      <div>
        <h2 className="text-2xl font-bold tracking-tight">{section.title}</h2>
        <p className="mt-2 text-muted-foreground">{section.overview}</p>
      </div>

      <Separator />

      {/* Concepts Grid */}
      {section.concepts.length > 0 && (
        <div>
          <h3 className="mb-4 text-lg font-semibold">Key Concepts</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            {section.concepts.map((concept) => {
              const Icon = getIcon(concept.icon);
              return (
                <Card key={concept.title} className="transition-colors hover:bg-accent/30">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                        <Icon className="h-5 w-5 text-primary" />
                      </div>
                      <CardTitle className="text-base">{concept.title}</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <CardDescription className="text-sm leading-relaxed">
                      {concept.description}
                    </CardDescription>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* Step-by-Step */}
      {section.steps.length > 0 && (
        <div>
          <h3 className="mb-4 text-lg font-semibold">Step by Step</h3>
          <div className="space-y-4">
            {section.steps.map((step, i) => (
              <div key={step.title} className="flex gap-4">
                <div className="flex flex-col items-center">
                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                    {i + 1}
                  </div>
                  {i < section.steps.length - 1 && (
                    <div className="mt-2 h-full w-px bg-border" />
                  )}
                </div>
                <div className="pb-6">
                  <h4 className="font-medium">{step.title}</h4>
                  <p className="mt-1 text-sm text-muted-foreground whitespace-pre-line">
                    {step.content}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Code Examples */}
      {section.examples.length > 0 && (
        <div>
          <h3 className="mb-4 text-lg font-semibold">Examples</h3>
          <div className="space-y-4">
            {section.examples.map((example) => (
              <div key={example.title}>
                <h4 className="mb-2 text-sm font-medium">{example.title}</h4>
                <CodeBlock code={example.code} language={example.language} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Troubleshooting FAQ */}
      {section.troubleshooting && section.troubleshooting.length > 0 && (
        <div>
          <h3 className="mb-4 text-lg font-semibold">Frequently Asked Questions</h3>
          <Accordion>
            {section.troubleshooting.map((faq) => (
              <AccordionItem key={faq.question}>
                <AccordionTrigger>{faq.question}</AccordionTrigger>
                <AccordionContent>
                  <p className="text-muted-foreground">{faq.answer}</p>
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      )}

      {/* Try It Now */}
      {section.tryIt && (
        <div>
          <Button
            size="lg"
            onClick={() => navigate(section.tryIt!.path)}
            className="gap-2"
          >
            {section.tryIt.label}
            <ExternalLink className="h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Related Sections */}
      {section.related.length > 0 && (
        <div>
          <Separator className="mb-6" />
          <h3 className="mb-3 text-sm font-semibold text-muted-foreground">
            Related Sections
          </h3>
          <div className="flex flex-wrap gap-2">
            {section.related.map((relatedId) => {
              const related = sectionMap[relatedId];
              if (!related) return null;
              return (
                <Badge
                  key={relatedId}
                  variant="secondary"
                  className="cursor-pointer gap-1 transition-colors hover:bg-secondary/60"
                  onClick={() => {
                    // Update selected section via query param
                    const url = new URL(window.location.href);
                    url.searchParams.set("section", relatedId);
                    window.history.pushState({}, "", url);
                    // Force re-render by dispatching popstate
                    window.dispatchEvent(new PopStateEvent("popstate"));
                  }}
                >
                  {related.title}
                  <ArrowRight className="h-3 w-3" />
                </Badge>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function GuidePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { searchQuery, setSearchQuery, startTour, tourCompleted } = useGuideStore();

  const initialSection = searchParams.get("section") ?? allSections[0]?.id ?? "getting-started";
  const [selectedId, setSelectedId] = useState(initialSection);

  // Listen for popstate to handle related section navigation
  useEffect(() => {
    function handlePopState() {
      const params = new URLSearchParams(window.location.search);
      const section = params.get("section");
      if (section && sectionMap[section]) {
        setSelectedId(section);
      }
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const handleSelectSection = useCallback(
    (sectionId: string) => {
      setSelectedId(sectionId);
      setSearchParams({ section: sectionId });
      setSearchQuery("");
    },
    [setSearchParams, setSearchQuery],
  );

  const filteredSections = useMemo(
    () => filterSections(searchQuery),
    [searchQuery],
  );

  const activeSection = sectionMap[selectedId] ?? allSections[0];

  return (
    <div className="flex h-full gap-6">
      {/* Sidebar - Table of Contents */}
      <div className="hidden w-64 shrink-0 lg:block">
        <div className="sticky top-0 space-y-4">
          {/* Header */}
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-primary" />
            <h2 className="font-semibold">User Guide</h2>
          </div>

          {/* Search */}
          <GuideSearch
            query={searchQuery}
            onQueryChange={setSearchQuery}
            onSelectSection={handleSelectSection}
            showResults={false}
          />

          {/* Replay Tour button */}
          <Button
            variant="outline"
            size="sm"
            className="w-full gap-2"
            onClick={startTour}
          >
            <PlayCircle className="h-4 w-4" />
            {tourCompleted ? "Replay Tour" : "Start Tour"}
          </Button>

          <Separator />

          {/* Section list */}
          <ScrollArea className="h-[calc(100vh-320px)]">
            <nav className="space-y-1">
              {filteredSections.map((section) => (
                <button
                  key={section.id}
                  onClick={() => handleSelectSection(section.id)}
                  className={cn(
                    "flex w-full items-center rounded-md px-3 py-2 text-left text-sm transition-colors",
                    selectedId === section.id
                      ? "bg-primary/10 font-medium text-primary"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  {section.title}
                </button>
              ))}
            </nav>
          </ScrollArea>
        </div>
      </div>

      {/* Mobile section selector */}
      <div className="block w-full lg:hidden">
        <div className="mb-4 space-y-3">
          <div className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-primary" />
            <h2 className="font-semibold">User Guide</h2>
            <Button
              variant="outline"
              size="sm"
              className="ml-auto gap-2"
              onClick={startTour}
            >
              <PlayCircle className="h-4 w-4" />
              Tour
            </Button>
          </div>

          <GuideSearch
            query={searchQuery}
            onQueryChange={setSearchQuery}
            onSelectSection={handleSelectSection}
          />

          {/* Horizontal section tabs on mobile */}
          <div className="flex gap-2 overflow-x-auto pb-2">
            {filteredSections.map((section) => (
              <button
                key={section.id}
                onClick={() => handleSelectSection(section.id)}
                className={cn(
                  "shrink-0 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  selectedId === section.id
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border text-muted-foreground hover:bg-accent",
                )}
              >
                {section.title}
              </button>
            ))}
          </div>
        </div>

        {/* Content on mobile */}
        {activeSection && <SectionContent section={activeSection} />}
      </div>

      {/* Main content - desktop */}
      <div className="hidden flex-1 lg:block">
        <ScrollArea className="h-[calc(100vh-140px)]">
          {activeSection && <SectionContent section={activeSection} />}
        </ScrollArea>
      </div>
    </div>
  );
}
