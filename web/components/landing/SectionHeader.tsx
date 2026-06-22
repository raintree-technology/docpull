import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

type SectionHeaderProps = {
  title: ReactNode;
  description?: ReactNode;
  className?: string;
  titleClassName?: string;
  descriptionClassName?: string;
  align?: "left" | "center";
};

export default function SectionHeader({
  title,
  description,
  className,
  titleClassName,
  descriptionClassName,
  align = "left",
}: SectionHeaderProps) {
  return (
    <div
      className={cn(
        align === "center" ? "text-center" : "text-center sm:text-left",
        className,
      )}
    >
      <h2
        className={cn(
          "mb-3 text-2xl font-semibold leading-tight sm:text-3xl",
          titleClassName,
        )}
      >
        {title}
      </h2>
      {description && (
        <p
          className={cn(
            "max-w-2xl text-base leading-7 text-muted-foreground",
            align === "center" && "mx-auto",
            descriptionClassName,
          )}
        >
          {description}
        </p>
      )}
    </div>
  );
}
