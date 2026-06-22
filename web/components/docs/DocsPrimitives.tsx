import type { ReactNode } from "react";
import { ArrowRight } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { DocsTableRow } from "./docs-data";

export function AnchorHeading({
  id,
  children,
}: {
  id: string;
  children: ReactNode;
}) {
  return (
    <h2
      id={id}
      className="scroll-mt-24 border-t pt-10 text-2xl font-semibold leading-8 tracking-normal text-foreground first:border-t-0 first:pt-0"
    >
      {children}
    </h2>
  );
}

export function InlineCode({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-md border bg-muted px-1.5 py-0.5 font-mono text-[0.9em] text-foreground">
      {children}
    </code>
  );
}

export function FactStrip({
  facts,
}: {
  facts: readonly { icon: LucideIcon; label: string }[];
}) {
  return (
    <div className="mt-6 flex flex-wrap gap-x-5 gap-y-3">
      {facts.map(({ icon: Icon, label }) => (
        <div
          key={label}
          className="inline-flex items-center gap-2 text-sm font-medium leading-5 text-muted-foreground"
        >
          <Icon className="h-4 w-4 text-teal-600 dark:text-teal-300" />
          {label}
        </div>
      ))}
    </div>
  );
}

export function DocsTable({
  rows,
}: {
  rows: readonly DocsTableRow[];
}) {
  return (
    <div className="my-5 overflow-hidden rounded-lg border">
      <table className="w-full border-collapse text-left text-sm leading-6">
        <thead className="bg-muted/70 text-foreground">
          <tr>
            <th className="w-40 border-b px-4 py-2.5 font-semibold">Name</th>
            <th className="border-b px-4 py-2.5 font-semibold">Use it for</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, description]) => (
            <tr key={name} className="border-b last:border-b-0">
              <td className="px-4 py-3 align-top font-mono text-[13px] font-semibold">
                {name}
              </td>
              <td className="px-4 py-3 align-top text-muted-foreground">
                {description}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Callout({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="my-5 rounded-lg border border-teal-200 bg-teal-50/70 p-4 dark:border-teal-900 dark:bg-teal-950/30">
      <div className="flex gap-3">
        <Icon
          className="mt-0.5 h-5 w-5 shrink-0 text-teal-700 dark:text-teal-300"
          aria-hidden="true"
        />
        <div>
          <p className="font-semibold leading-6 text-foreground">{title}</p>
          <div className="mt-1 text-sm leading-6 text-muted-foreground">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

export function ResourceCard({
  href,
  icon: Icon,
  title,
  children,
}: {
  href: string;
  icon: LucideIcon;
  title: string;
  children: ReactNode;
}) {
  const isExternal = href.startsWith("http");

  return (
    <a
      href={href}
      target={isExternal ? "_blank" : undefined}
      rel={isExternal ? "noopener noreferrer" : undefined}
      className="group rounded-lg border p-4 transition-colors hover:border-teal-500/60 hover:bg-muted/40"
    >
      <Icon
        className="h-5 w-5 text-teal-700 dark:text-teal-300"
        aria-hidden="true"
      />
      <p className="mt-3 font-semibold leading-6">{title}</p>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">{children}</p>
      <span className="mt-3 inline-flex items-center gap-1 text-sm font-semibold leading-5 text-teal-700 dark:text-teal-300">
        Open
        <ArrowRight
          className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5"
          aria-hidden="true"
        />
      </span>
    </a>
  );
}
