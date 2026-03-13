export interface ConceptCard {
  title: string;
  description: string;
  icon: string;
}

export interface StepByStep {
  title: string;
  content: string;
}

export interface CodeExample {
  title: string;
  language: string;
  code: string;
}

export interface TryItAction {
  label: string;
  path: string;
}

export interface FAQ {
  question: string;
  answer: string;
}

export interface GuideSection {
  id: string;
  title: string;
  overview: string;
  concepts: ConceptCard[];
  steps: StepByStep[];
  examples: CodeExample[];
  tryIt?: TryItAction;
  troubleshooting?: FAQ[];
  related: string[];
}

export { gettingStarted } from "./getting-started";
export { configReference } from "./config-reference";
export { toolsGuide } from "./tools-guide";
export { chatGuide } from "./chat-guide";
export { peersGuide } from "./peers-guide";
export { securityGuide } from "./security-guide";
export { troubleshooting } from "./troubleshooting";
export { glossary } from "./glossary";

import { gettingStarted } from "./getting-started";
import { configReference } from "./config-reference";
import { toolsGuide } from "./tools-guide";
import { chatGuide } from "./chat-guide";
import { peersGuide } from "./peers-guide";
import { securityGuide } from "./security-guide";
import { troubleshooting } from "./troubleshooting";
import { glossary } from "./glossary";

export const allSections: GuideSection[] = [
  gettingStarted,
  configReference,
  toolsGuide,
  chatGuide,
  peersGuide,
  securityGuide,
  troubleshooting,
  glossary,
];

export const sectionMap: Record<string, GuideSection> = Object.fromEntries(
  allSections.map((s) => [s.id, s]),
);
