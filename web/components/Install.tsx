"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { CommandCopy, LandingSection } from "@/components/landing";

const altMethods = [
  { label: "pipx", command: "pipx install docpull" },
  { label: "uv", command: "uv pip install docpull" },
  { label: "+parallel", command: "pip install 'docpull[parallel]'" },
  { label: "+proxy", command: "pip install 'docpull[proxy]'" },
  { label: "+all", command: "pip install 'docpull[all]'" },
] as const;

export default function Install() {
  const [showAlt, setShowAlt] = useState(false);

  return (
    <LandingSection
      id="install"
      title="Install"
      description="Install once, then crawl from your terminal, scripts, or agent workflow. Requires Python 3.10 or newer."
      align="center"
      containerClassName="max-w-2xl text-center"
      headerClassName="mb-6"
    >
      <div className="mb-6 flex flex-wrap items-center justify-center gap-2">
        <CommandCopy command="pip install docpull" />
      </div>

      <button
        type="button"
        onClick={() => setShowAlt(!showAlt)}
        className="inline-flex min-h-11 items-center gap-1.5 text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
        aria-expanded={showAlt}
      >
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 transition-transform",
            showAlt && "rotate-180",
          )}
        />
        More options
      </button>

      {showAlt && (
        <div className="mx-auto mt-4 grid max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
          {altMethods.map((method) => (
            <CommandCopy
              key={method.label}
              command={method.command}
              label={`Copy ${method.label} install command`}
              prefix={method.label}
              className="max-w-none shadow-none sm:w-full"
              codeClassName="py-2.5 text-[13px] leading-5"
              buttonClassName="h-10 w-10"
            />
          ))}
        </div>
      )}
    </LandingSection>
  );
}
