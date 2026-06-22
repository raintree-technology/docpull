import { cn } from "@/lib/utils";

type Stat = {
  label: string;
  value: string;
};

type StatGridProps = {
  stats: readonly Stat[];
  className?: string;
};

export default function StatGrid({ stats, className }: StatGridProps) {
  return (
    <dl
      className={cn(
        "grid gap-px overflow-hidden rounded-lg border bg-border",
        className,
      )}
    >
      {stats.map((stat) => (
        <div key={stat.label} className="bg-background/85 px-4 py-3.5">
          <dt className="text-xs font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            {stat.label}
          </dt>
          <dd className="mt-1 text-[15px] font-semibold leading-6">
            {stat.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
