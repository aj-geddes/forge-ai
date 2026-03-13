import { type DetailsHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";

function Accordion({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cn("w-full", className)} {...props}>
      {children}
    </div>
  );
}

function AccordionItem({
  className,
  children,
  ...props
}: DetailsHTMLAttributes<HTMLDetailsElement>) {
  return (
    <details
      className={cn("group border-b", className)}
      {...props}
    >
      {children}
    </details>
  );
}

interface AccordionTriggerProps extends HTMLAttributes<HTMLElement> {
  children: ReactNode;
}

function AccordionTrigger({ className, children, ...props }: AccordionTriggerProps) {
  return (
    <summary
      className={cn(
        "flex flex-1 cursor-pointer list-none items-center justify-between py-4 font-medium transition-all hover:underline [&::-webkit-details-marker]:hidden",
        className,
      )}
      {...props}
    >
      {children}
      <ChevronDown className="h-4 w-4 shrink-0 transition-transform duration-200 group-open:rotate-180" />
    </summary>
  );
}

function AccordionContent({
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("overflow-hidden pb-4 pt-0 text-sm", className)}
      {...props}
    >
      {children}
    </div>
  );
}

export { Accordion, AccordionItem, AccordionTrigger, AccordionContent };
