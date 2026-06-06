"use client";

import { useCallback, useState, type KeyboardEvent } from "react";
import { Check, Copy } from "lucide-react";
import { claudePluginInstall, mcpSetups } from "@/lib/content/install";
import { cn } from "@/lib/utils";
import { useCopyToClipboard } from "@/lib/hooks/use-copy-to-clipboard";
import { HostTab } from "./HostBadge";

export default function McpSetup() {
  const [active, setActive] =
    useState<(typeof mcpSetups)[number]["id"]>("claude-code");
  const { copiedId, copy } = useCopyToClipboard();

  const activeSetup =
    mcpSetups.find((setup) => setup.id === active) ?? mcpSetups[0];
  const activeIndex = mcpSetups.findIndex((setup) => setup.id === active);

  const handleTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }

      event.preventDefault();
      const lastIndex = mcpSetups.length - 1;
      const nextIndex =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? lastIndex
            : event.key === "ArrowRight"
              ? activeIndex === lastIndex
                ? 0
                : activeIndex + 1
              : activeIndex === 0
                ? lastIndex
                : activeIndex - 1;
      const nextId = mcpSetups[nextIndex].id;

      setActive(nextId);
      document.getElementById(`mcp-tab-${nextId}`)?.focus();
    },
    [activeIndex],
  );

  return (
    <section id="mcp" className="border-t border-foreground/8 py-16 sm:py-24">
      <div className="mx-auto max-w-6xl px-6">
        <div className="mb-10 max-w-3xl sm:mb-14">
          <p className="section-kicker mb-3">Client setup</p>
          <h2 className="section-title mb-4">
            Set up docpull in your client.
          </h2>
          <p className="section-copy">
            Use the Claude Code plugin if you want MCP prompt commands and the
            bundled research skill. Otherwise connect the local MCP server
            directly in Claude Code, Cursor, or Codex.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] gap-5 sm:gap-6">
          <article className="apple-panel rounded-[1.75rem] p-5 sm:p-6">
            <h3 className="text-2xl font-medium tracking-tight text-foreground mb-3">
              Add the Claude plugin.
            </h3>
            <p className="text-sm sm:text-base text-foreground/74 leading-relaxed mb-5">
              This bundles the MCP server, MCP prompt commands, and the
              repo&apos;s docpull research skill for Claude Code. It is the
              most opinionated setup, which is usually what you want if the end
              goal is agent-assisted web research with a strong local-source
              workflow.
            </p>

            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <p className="text-sm text-foreground/68 mt-2 leading-relaxed">
                  Install once, then the bundled MCP prompts are available
                  inside Claude Code.
                </p>
              </div>
              <button
                type="button"
                onClick={() => copy(claudePluginInstall, "claude-plugin")}
                className="min-h-11 min-w-11 shrink-0 p-2.5 rounded-xl glass hover:bg-foreground/5 transition-colors"
                aria-label={copiedId === "claude-plugin" ? "Copied" : "Copy setup"}
              >
                {copiedId === "claude-plugin" ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>

            <pre className="overflow-x-auto rounded-[1.25rem] border border-foreground/10 bg-background/55 p-4 text-xs sm:text-sm">
              <code className="whitespace-pre text-foreground/82">
                {claudePluginInstall}
              </code>
            </pre>
          </article>

          <article className="apple-panel rounded-[1.75rem] p-5 sm:p-6">
            <div
              className="mb-5 flex flex-wrap gap-2 rounded-[1.2rem] border border-foreground/8 bg-background/45 p-2"
              role="tablist"
              aria-label="MCP hosts"
            >
              {mcpSetups.map((setup) => (
                <button
                  type="button"
                  key={setup.id}
                  id={`mcp-tab-${setup.id}`}
                  onClick={() => setActive(setup.id)}
                  onKeyDown={handleTabKeyDown}
                  role="tab"
                  aria-selected={active === setup.id}
                  aria-controls={`mcp-panel-${setup.id}`}
                  tabIndex={active === setup.id ? 0 : -1}
                  className={cn(
                    "min-h-11 rounded-full px-3 py-2 text-sm transition-colors",
                    active === setup.id
                      ? "bg-foreground text-background"
                      : "bg-background/40 text-foreground/60 hover:text-foreground",
                  )}
                >
                  <HostTab
                    brand={setup.brand}
                    label={setup.label}
                    active={active === setup.id}
                  />
                </button>
              ))}
            </div>

            <div
              id={`mcp-panel-${activeSetup.id}`}
              role="tabpanel"
              aria-labelledby={`mcp-tab-${activeSetup.id}`}
              className="flex items-start justify-between gap-4 mb-4"
            >
              <div>
                <h3 className="text-xl font-medium tracking-tight text-foreground mt-2">
                  {activeSetup.label}
                </h3>
                <p className="text-sm sm:text-base text-foreground/70 mt-2 leading-relaxed">
                  {activeSetup.note}
                </p>
              </div>
              <button
                type="button"
                onClick={() => copy(activeSetup.code, activeSetup.id)}
                className="min-h-11 min-w-11 shrink-0 p-2.5 rounded-xl glass hover:bg-foreground/5 transition-colors"
                aria-label={copiedId === activeSetup.id ? "Copied" : "Copy setup"}
              >
                {copiedId === activeSetup.id ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>

            <pre className="overflow-x-auto rounded-[1.25rem] border border-foreground/10 bg-background/55 p-4 text-xs sm:text-sm">
              <code className="whitespace-pre text-foreground/82">
                {activeSetup.code}
              </code>
            </pre>
          </article>
        </div>
      </div>
    </section>
  );
}
