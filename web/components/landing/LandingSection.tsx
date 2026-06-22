import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import SectionHeader from "./SectionHeader";

type LandingSectionProps = {
  id?: string;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  bordered?: boolean;
  align?: "left" | "center";
  className?: string;
  containerClassName?: string;
  headerClassName?: string;
  titleClassName?: string;
  descriptionClassName?: string;
};

export default function LandingSection({
  id,
  title,
  description,
  children,
  bordered = true,
  align = "left",
  className,
  containerClassName,
  headerClassName,
  titleClassName,
  descriptionClassName,
}: LandingSectionProps) {
  return (
    <section
      id={id}
      className={cn("py-14 sm:py-20", bordered && "border-t", className)}
    >
      <div className={cn("mx-auto max-w-5xl px-6", containerClassName)}>
        {title && (
          <SectionHeader
            title={title}
            description={description}
            align={align}
            className={cn("mb-8 sm:mb-12", headerClassName)}
            titleClassName={titleClassName}
            descriptionClassName={descriptionClassName}
          />
        )}
        {children}
      </div>
    </section>
  );
}
