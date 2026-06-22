"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { faqs } from "./faq-content";
import { GlassPanel, LandingSection } from "@/components/landing";

function FaqItem({ q, a, index }: { q: string; a: ReactNode; index: number }) {
  const [open, setOpen] = useState(false);
  const buttonId = `faq-button-${index}`;
  const panelId = `faq-panel-${index}`;

  return (
    <div className="border-b last:border-b-0">
      <button
        id={buttonId}
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-4 text-left gap-4"
        aria-expanded={open}
        aria-controls={panelId}
      >
        <span className="text-base font-semibold leading-6">{q}</span>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-muted-foreground shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-labelledby={buttonId}
          className="pb-5 pr-8 text-[15px] leading-7 text-muted-foreground"
        >
          {a}
        </div>
      )}
    </div>
  );
}

export default function FAQ() {
  return (
    <LandingSection
      id="faq"
      title="Why docpull?"
      description="Answers to questions people ask before installing."
      containerClassName="max-w-3xl"
    >
      <GlassPanel className="px-5">
        {faqs.map((faq, i) => (
          <FaqItem key={i} q={faq.q} a={faq.a} index={i} />
        ))}
      </GlassPanel>
    </LandingSection>
  );
}
