import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import GlassPanel from "./GlassPanel";

type InfoCardProps = {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
  children?: ReactNode;
  className?: string;
  titleClassName?: string;
  descriptionClassName?: string;
};

export default function InfoCard({
  title,
  description,
  meta,
  children,
  className,
  titleClassName,
  descriptionClassName,
}: InfoCardProps) {
  return (
    <GlassPanel className={cn("p-5", className)}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className={cn("text-base font-semibold leading-6", titleClassName)}>
          {title}
        </h3>
        {meta && (
          <div className="font-mono text-xs leading-5 text-muted-foreground">
            {meta}
          </div>
        )}
      </div>
      {description && (
        <p
          className={cn(
            "text-[15px] leading-7 text-muted-foreground",
            descriptionClassName,
          )}
        >
          {description}
        </p>
      )}
      {children}
    </GlassPanel>
  );
}
