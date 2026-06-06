"use client";

import { useState } from "react";
import { Copy, Check, ChevronDown } from "lucide-react";
import { altInstallMethods, installCommand } from "@/lib/content/install";
import { cn } from "@/lib/utils";
import { useCopyToClipboard } from "@/lib/hooks/use-copy-to-clipboard";

export default function Install() {
  const [showAlt, setShowAlt] = useState(false);
  const { copiedId, copy } = useCopyToClipboard();
  const altPanelId = "install-alternatives";

  return (
    <section
      id="install"
      className="border-t border-foreground/8 py-16 sm:py-24"
    >
      <div className="mx-auto max-w-5xl px-6">
        <div className="mx-auto max-w-3xl text-center">
          <p className="section-kicker mb-3">Start local</p>
          <h2 className="section-title mb-4">
            One install command, then point it at a site.
          </h2>
          <p className="section-copy mb-3">
            The default path is intentionally boring: install the package, run
            it against a URL, keep the output on your machine.
          </p>
          <p className="text-sm sm:text-base text-foreground/58 mb-6">
            Requires Python 3.10 or newer.
          </p>
          {/* Main pip command */}
          <div className="mb-6 flex flex-wrap items-center justify-center gap-2">
            <code className="apple-panel rounded-full px-6 py-3 text-sm font-mono sm:text-base">
              {installCommand}
            </code>
            <button
              type="button"
              onClick={() => copy(installCommand, "main")}
              className="apple-panel min-h-11 min-w-11 rounded-full p-3 transition-colors hover:bg-foreground/5"
              aria-label={copiedId === "main" ? "Copied" : "Copy command"}
            >
              {copiedId === "main" ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </button>
          </div>

          {/* Collapsible alternatives */}
          <div>
            <button
              type="button"
              onClick={() => setShowAlt(!showAlt)}
              className="inline-flex min-h-11 items-center gap-1.5 rounded-full px-4 text-sm text-foreground/65 transition-colors hover:bg-background/60 hover:text-foreground"
              aria-expanded={showAlt}
              aria-controls={altPanelId}
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
              <div
                id={altPanelId}
                className="mx-auto mt-4 grid max-w-lg grid-cols-1 gap-2 sm:grid-cols-2"
              >
                {altInstallMethods.map((method, i) => (
                  <div
                    key={method.label}
                    className="apple-panel flex items-center justify-between rounded-[1.25rem] px-4 py-2.5 text-sm"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-muted-foreground text-xs font-medium min-w-[32px]">
                        {method.label}
                      </span>
                      <code className="font-mono text-xs">
                        {method.command}
                      </code>
                    </div>
                    <button
                      type="button"
                      onClick={() => copy(method.command, `alt-${i}`)}
                      className="min-h-11 min-w-11 p-2 rounded-lg hover:bg-foreground/5 transition-colors ml-2"
                      aria-label={
                        copiedId === `alt-${i}` ? "Copied" : "Copy command"
                      }
                    >
                      {copiedId === `alt-${i}` ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
