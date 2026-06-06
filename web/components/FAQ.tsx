"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { faqs } from "@/lib/content/faqs";

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
        <span className="text-sm font-medium text-foreground">{q}</span>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-foreground/50 shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div
          id={panelId}
          role="region"
          aria-labelledby={buttonId}
          className="pb-4 text-sm text-foreground/72 leading-relaxed pr-8"
        >
          {a}
        </div>
      )}
    </div>
  );
}

export default function FAQ() {
  return (
    <section id="faq" className="border-t border-foreground/8 py-16 sm:py-24">
      <div className="mx-auto max-w-3xl px-6">
        <div className="mb-8 max-w-2xl text-center sm:text-left">
          <p className="section-kicker mb-3">Sharp edges</p>
          <h2 className="section-title mb-4">
            The questions people ask right before they try it.
          </h2>
          <p className="section-copy">
            This is where the practical constraints belong: JavaScript-heavy
            sites, auth, MCP usage, and what the fetcher will or will not do.
          </p>
        </div>

        <div className="apple-panel rounded-[1.75rem] px-5">
          {faqs.map((faq, i) => (
            <FaqItem key={faq.q} q={faq.q} a={faq.a} index={i} />
          ))}
        </div>
      </div>
    </section>
  );
}
