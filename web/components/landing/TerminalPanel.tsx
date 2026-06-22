import { cn } from "@/lib/utils";

export type TerminalLine = {
  type: "command" | "output" | "dim" | "normal" | "success" | "error";
  content: string;
};

type TerminalPanelProps = {
  lines: readonly TerminalLine[];
  title?: string;
  className?: string;
  bodyClassName?: string;
};

export default function TerminalPanel({
  lines,
  title,
  className,
  bodyClassName,
}: TerminalPanelProps) {
  return (
    <div className={cn("terminal overflow-hidden", className)}>
      <div className="terminal-header justify-between">
        <div className="flex items-center gap-2">
          <div className="terminal-dot terminal-dot-close" />
          <div className="terminal-dot terminal-dot-minimize" />
          <div className="terminal-dot terminal-dot-maximize" />
        </div>
        {title && (
          <span className="font-mono text-xs leading-5 text-neutral-400">
            {title}
          </span>
        )}
      </div>
      <div
        className={cn(
          "min-h-[170px] p-4 font-mono text-[13px] leading-6 sm:min-h-[230px] sm:p-5 sm:text-[15px] lg:p-6",
          bodyClassName,
        )}
      >
        {lines.map((line, i) => (
          <div
            key={`${line.type}-${i}`}
            className={cn(
              "mb-0.5 break-words",
              line.type === "command" && "text-white",
              line.type === "dim" && "text-neutral-400",
              line.type === "normal" && "text-neutral-300",
              line.type === "success" && "text-emerald-300",
              line.type === "error" && "text-red-300",
              line.type === "output" && "h-3 sm:h-4",
            )}
          >
            {line.type === "command" && (
              <span className="text-neutral-400">$ </span>
            )}
            {line.content}
          </div>
        ))}
      </div>
    </div>
  );
}
