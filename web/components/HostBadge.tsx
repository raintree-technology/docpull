import Image from "next/image";
import { cn } from "@/lib/utils";

type Brand = "anthropic" | "cursor" | "openai";

function BrandLogo({
  brand,
  alt,
  className,
  reversed = false,
  variant = "wordmark",
}: {
  brand: Brand;
  alt: string;
  className?: string;
  reversed?: boolean;
  variant?: "wordmark" | "symbol";
}) {
  const hasDedicatedSymbol = brand !== "cursor";
  const useSymbol = variant === "symbol" && hasDedicatedSymbol;
  const suffix = useSymbol ? "symbol" : "";
  const path = (theme: "light" | "dark") =>
    `/brands/${brand}${suffix ? `-${suffix}` : ""}-${theme}.svg`;

  if (brand === "cursor" && variant === "symbol") {
    return (
      <span className={cn("inline-flex items-center", className)}>
        <Image
          src="/brands/cursor-symbol.svg"
          alt={alt}
          width={18}
          height={18}
          unoptimized
          className="block h-[18px] w-[18px] dark:hidden"
        />
        <Image
          src="/brands/cursor-symbol.svg"
          alt={alt}
          width={18}
          height={18}
          unoptimized
          className="hidden h-[18px] w-[18px] dark:block"
        />
      </span>
    );
  }

  return (
    <span className={cn("inline-flex items-center", className)}>
      <Image
        src={path(reversed ? "light" : "dark")}
        alt={alt}
        width={useSymbol ? 18 : 80}
        height={useSymbol ? 18 : 16}
        unoptimized
        className={cn(
          "block w-auto dark:hidden",
          useSymbol ? "h-[18px]" : "h-4",
        )}
      />
      <Image
        src={path(reversed ? "dark" : "light")}
        alt={alt}
        width={useSymbol ? 18 : 80}
        height={useSymbol ? 18 : 16}
        unoptimized
        className={cn(
          "hidden w-auto dark:block",
          useSymbol ? "h-[18px]" : "h-4",
        )}
      />
    </span>
  );
}

export function HostTab({
  brand,
  label,
  active = false,
}: {
  brand: Brand;
  label: string;
  active?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <BrandLogo
        brand={brand}
        alt={label}
        reversed={active}
        variant="symbol"
      />
      <span>{label}</span>
    </span>
  );
}
