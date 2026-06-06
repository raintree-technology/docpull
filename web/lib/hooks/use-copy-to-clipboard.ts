"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_RESET_DELAY_MS = 2_000;

export function useCopyToClipboard(resetDelayMs = DEFAULT_RESET_DELAY_MS) {
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [copyFailed, setCopyFailed] = useState(false);
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (resetTimer.current) {
        clearTimeout(resetTimer.current);
      }
    };
  }, []);

  const copy = useCallback(
    async (text: string, id: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopiedId(id);
        setCopyFailed(false);

        if (resetTimer.current) {
          clearTimeout(resetTimer.current);
        }

        resetTimer.current = setTimeout(() => {
          setCopiedId(null);
          resetTimer.current = null;
        }, resetDelayMs);
      } catch {
        setCopiedId(null);
        setCopyFailed(true);
      }
    },
    [resetDelayMs],
  );

  return { copiedId, copyFailed, copy };
}
