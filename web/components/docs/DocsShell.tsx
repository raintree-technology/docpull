import type { ReactNode } from "react";
import Link from "next/link";
import { ExternalLink, Search } from "lucide-react";
import { BrandMark } from "@/components/landing";
import { GithubIcon } from "@/components/GithubIcon";
import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";
import { docsNav, toc } from "./docs-data";

function DocsHeader() {
  return (
    <header className="sticky top-0 z-50 border-b bg-background/92 backdrop-blur-md">
      <div className="mx-auto grid h-14 max-w-[90rem] grid-cols-[auto_1fr_auto] items-center gap-4 px-4 sm:px-6">
        <Link
          href="/"
          className="inline-flex min-h-11 items-center gap-2 text-[15px] font-semibold leading-5"
        >
          <BrandMark iconClassName="h-4.5 w-4.5" />
        </Link>

        <nav className="hidden items-center gap-5 md:flex">
          <Link
            href="/docs"
            className="inline-flex min-h-11 items-center border-b-2 border-teal-500 px-1 text-[15px] font-semibold leading-5"
          >
            Docs
          </Link>
          <Link
            href="/#examples"
            className="inline-flex min-h-11 items-center text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Examples
          </Link>
          <Link
            href="/#install"
            className="inline-flex min-h-11 items-center text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Install
          </Link>
        </nav>

        <div className="flex items-center justify-end gap-1">
          <form
            action="https://github.com/raintree-technology/docpull/search"
            method="get"
            target="_blank"
            className="hidden lg:block"
          >
            <input type="hidden" name="type" value="code" />
            <label className="relative block">
              <span className="sr-only">Search docs on GitHub</span>
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <input
                name="q"
                type="search"
                placeholder="Search docs..."
                className="h-9 w-64 rounded-lg border bg-background pl-9 pr-14 text-sm leading-5 outline-hidden transition-colors placeholder:text-muted-foreground focus:border-teal-500 focus:ring-2 focus:ring-teal-500/15"
              />
              <span className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 rounded border bg-muted px-1.5 py-0.5 text-[11px] font-medium leading-4 text-muted-foreground xl:block">
                GitHub
              </span>
            </label>
          </form>

          <a
            href="https://github.com/raintree-technology/docpull"
            target="_blank"
            rel="noopener noreferrer"
            className="flex h-11 w-11 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            aria-label="GitHub repository"
          >
            <GithubIcon className="h-4 w-4" />
          </a>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}

function DocsSidebar() {
  return (
    <aside className="hidden min-h-0 border-r bg-background/78 lg:block">
      <div className="h-full overflow-y-auto px-4 py-8">
        <p className="mb-7 px-2 text-[15px] font-semibold leading-5">
          Documentation
        </p>
        <nav aria-label="Docs sidebar" className="space-y-7">
          {docsNav.map((group) => (
            <div key={group.title}>
              <p className="px-2 text-xs font-semibold uppercase leading-5 tracking-wide text-muted-foreground">
                {group.title}
              </p>
              <ul className="mt-2 space-y-1">
                {group.items.map((item) => {
                  const isActive = "active" in item && item.active;

                  return (
                    <li key={item.href}>
                      <a
                        href={item.href}
                        className={cn(
                          "block rounded-md px-2 py-1.5 text-[15px] font-medium leading-6 transition-colors",
                          isActive
                            ? "bg-teal-50 text-teal-800 dark:bg-teal-950/50 dark:text-teal-200"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        {item.label}
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>

        <div className="mt-8 rounded-lg border bg-background p-4">
          <div className="flex items-center gap-2 text-sm font-semibold leading-5">
            <GithubIcon className="h-4 w-4" aria-hidden="true" />
            Open source
          </div>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            Star the repository or open an issue if the docs need a missing
            recipe.
          </p>
          <a
            href="https://github.com/raintree-technology/docpull"
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-md border px-3 text-sm font-semibold leading-5 transition-colors hover:bg-muted"
          >
            Star on GitHub
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </a>
        </div>
      </div>
    </aside>
  );
}

function OnThisPage() {
  return (
    <aside className="hidden min-h-0 border-l xl:block">
      <div className="h-full overflow-y-auto px-6 py-10">
        <p className="text-xs font-semibold uppercase leading-5 tracking-wide text-muted-foreground">
          On This Page
        </p>
        <nav aria-label="On this page" className="mt-4">
          <ol className="space-y-3">
            {toc.map((item, index) => (
              <li key={item.href}>
                <a
                  href={item.href}
                  className="grid grid-cols-[1.5rem_1fr] gap-1 text-sm font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span>{index + 1}.</span>
                  <span>{item.label}</span>
                </a>
              </li>
            ))}
          </ol>
        </nav>
      </div>
    </aside>
  );
}

export function DocsShell({ children }: { children: ReactNode }) {
  return (
    <div className="h-screen overflow-hidden bg-background text-foreground">
      <DocsHeader />
      <div className="mx-auto grid h-[calc(100vh-3.5rem)] min-h-0 max-w-[90rem] overflow-hidden lg:grid-cols-[17rem_minmax(0,1fr)] xl:grid-cols-[17rem_minmax(0,55rem)_18rem]">
        <DocsSidebar />
        <main className="min-h-0 min-w-0 overflow-y-auto overscroll-contain px-4 py-8 sm:px-8 lg:px-10 lg:py-10">
          <article className="mx-auto max-w-3xl">{children}</article>
        </main>
        <OnThisPage />
      </div>
    </div>
  );
}
