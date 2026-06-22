"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

const COPY_RESET_DELAY_MS = 2_000;

type DocsCodeBlockProps = {
  code: string;
  language?: string;
  title?: string;
  className?: string;
};

export default function DocsCodeBlock({
  code,
  language = "bash",
  title,
  className,
}: DocsCodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current) {
        clearTimeout(resetTimer.current);
      }
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopied(false);
      setCopyFailed(true);
    }

    if (resetTimer.current) {
      clearTimeout(resetTimer.current);
    }

    resetTimer.current = setTimeout(() => {
      setCopied(false);
      setCopyFailed(false);
      resetTimer.current = null;
    }, COPY_RESET_DELAY_MS);
  }, [code]);

  return (
    <div
      className={cn(
        "my-4 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950 shadow-sm",
        className,
      )}
    >
      <div className="flex min-h-10 items-center justify-between border-b border-white/10 bg-white/[0.03] px-3">
        <span className="text-xs font-medium leading-5 text-zinc-400">
          {title ?? language}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex min-h-9 items-center gap-2 rounded-md px-2 text-xs font-medium leading-5 text-zinc-300 transition-colors hover:bg-white/10 hover:text-white focus:outline-hidden focus-visible:ring-1 focus-visible:ring-white/30"
          aria-label={copyFailed ? "Copy failed" : copied ? "Copied" : "Copy code"}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <Copy className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {copyFailed ? "Failed" : copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="overflow-x-auto p-4 text-[13px] leading-6 text-zinc-100">
        <code>{code}</code>
      </pre>
    </div>
  );
}
