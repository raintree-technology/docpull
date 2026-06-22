import { FileText } from "lucide-react";
import { cn } from "@/lib/utils";

type BrandMarkProps = {
  className?: string;
  iconClassName?: string;
};

export default function BrandMark({
  className,
  iconClassName,
}: BrandMarkProps) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <FileText className={cn("h-4 w-4", iconClassName)} aria-hidden="true" />
      <span>docpull</span>
    </span>
  );
}
