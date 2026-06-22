import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export default function GlassPanel({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("glass rounded-lg", className)} {...props} />;
}
