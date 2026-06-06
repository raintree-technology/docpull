import { GithubIcon } from "./GithubIcon";

function XIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

function RaintreeLogo({
  size = 16,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className={className}
    >
      <g stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" fill="none">
        <line x1="16" y1="29" x2="16" y2="5" />
        <line x1="16" y1="22" x2="24" y2="14" />
        <line x1="16" y1="22" x2="8" y2="14" />
        <line x1="16" y1="16" x2="22" y2="10" />
        <line x1="16" y1="16" x2="10" y2="10" />
        <line x1="16" y1="10" x2="20" y2="6" />
        <line x1="16" y1="10" x2="12" y2="6" />
      </g>
    </svg>
  );
}

const links = [
  {
    label: "PyPI",
    href: "https://pypi.org/project/docpull/",
  },
  {
    label: "README",
    href: "https://github.com/raintree-technology/docpull#readme",
  },
  {
    label: "llms.txt",
    href: "/llms.txt",
  },
  {
    label: "llms-full.txt",
    href: "/llms-full.txt",
  },
  {
    label: "Agent Skills",
    href: "/.well-known/agent-skills.json",
  },
  {
    label: "RSS",
    href: "/rss.xml",
  },
  {
    label: "security.txt",
    href: "/.well-known/security.txt",
  },
  {
    label: "Sitemap",
    href: "/sitemap.xml",
  },
] as const;

export default function Footer() {
  return (
    <footer className="relative z-10 border-t border-foreground/8 bg-background/90 py-10 sm:py-12">
      <div className="mx-auto max-w-6xl px-6">
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] gap-8">
          <div>
            <div className="flex items-center gap-2 mb-3 text-sm font-medium">
              <svg
                width="16"
                height="16"
                viewBox="0 0 32 32"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
                aria-hidden="true"
              >
                <path
                  d="M8 6h12l6 6v14a2 2 0 01-2 2H8a2 2 0 01-2-2V8a2 2 0 012-2z"
                  stroke="currentColor"
                  strokeWidth="2"
                  fill="none"
                />
                <path
                  d="M20 6v6h6"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              docpull
            </div>

            <h2 className="mb-3 text-2xl font-medium tracking-tight text-foreground sm:text-3xl">
              Local web pulls should stay inspectable.
            </h2>
            <p className="max-w-2xl text-sm sm:text-base text-foreground/72 leading-relaxed">
              Pull the site, keep the files, inspect the Markdown, and wire the
              result into the rest of your stack without a hosted black box in
              the middle.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:gap-4">
            {links.map((link) => (
              <a
                key={link.label}
                href={link.href}
                className="rounded-[1.1rem] border border-foreground/10 bg-foreground/[0.025] px-4 py-3 text-sm text-foreground/68 transition-colors hover:text-foreground"
              >
                <span className="block text-[10px] font-mono uppercase tracking-[0.16em] text-foreground/48 mb-1.5">
                  Link
                </span>
                {link.label}
              </a>
            ))}
          </div>
        </div>

        <div className="mt-8 pt-5 border-t border-foreground/10 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs sm:text-sm text-foreground/56">
            Open source by{" "}
            <a
              href="https://raintree.technology"
              target="_blank"
              rel="noopener noreferrer"
              className="text-foreground/72 hover:text-foreground transition-colors"
            >
              Raintree Technology
            </a>
            .
          </p>

          <div className="flex items-center gap-2 text-foreground/56">
            <a
              href="https://x.com/raintree_tech"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg hover:bg-foreground/[0.04] hover:text-foreground transition-colors"
              aria-label="X"
            >
              <XIcon className="h-4 w-4" />
            </a>
            <a
              href="https://github.com/raintree-technology"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg hover:bg-foreground/[0.04] hover:text-foreground transition-colors"
              aria-label="GitHub"
            >
              <GithubIcon className="h-4 w-4" />
            </a>
            <a
              href="https://raintree.technology"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center rounded-lg hover:bg-foreground/[0.04] hover:text-foreground transition-colors"
              aria-label="Raintree Technology"
            >
              <RaintreeLogo size={22} className="-mt-0.5 -ml-0.5" />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
