"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

const COPY_RESET_DELAY_MS = 2_000;

type CommandCopyProps = {
  command: string;
  label?: string;
  className?: string;
  codeClassName?: string;
  buttonClassName?: string;
  prefix?: ReactNode;
};

export default function CommandCopy({
  command,
  label = "Copy command",
  className,
  codeClassName,
  buttonClassName,
  prefix,
}: CommandCopyProps) {
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
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setCopyFailed(false);
      if (resetTimer.current) {
        clearTimeout(resetTimer.current);
      }
      resetTimer.current = setTimeout(() => {
        setCopied(false);
        resetTimer.current = null;
      }, COPY_RESET_DELAY_MS);
    } catch (error) {
      setCopied(false);
      setCopyFailed(error !== undefined);
    }
  }, [command]);

  return (
    <div
      className={cn(
        "inline-flex w-full max-w-sm items-center overflow-hidden rounded-lg border bg-background/85 shadow-sm sm:w-auto",
        className,
      )}
    >
      {prefix && (
        <span className="shrink-0 pl-4 pr-1 text-sm font-medium leading-5 text-muted-foreground">
          {prefix}
        </span>
      )}
      <code
        className={cn(
          "min-w-0 flex-1 overflow-x-auto whitespace-nowrap px-3 py-3 font-mono text-[15px] leading-6",
          codeClassName,
        )}
      >
        {command}
      </code>
      <button
        type="button"
        onClick={handleCopy}
        className={cn(
          "flex h-11 w-11 shrink-0 items-center justify-center border-l text-muted-foreground transition-colors hover:bg-muted hover:text-foreground",
          buttonClassName,
        )}
        aria-label={copyFailed ? "Copy failed" : copied ? "Copied" : label}
      >
        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
      </button>
    </div>
  );
}
