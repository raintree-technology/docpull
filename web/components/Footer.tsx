import { GithubIcon } from "./GithubIcon";
import { BrandMark } from "@/components/landing";

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

export default function Footer() {
  return (
    <footer className="border-t py-10 sm:py-12 relative z-10 bg-background">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 max-w-md mx-auto text-center sm:mx-0 sm:text-left">
          <BrandMark className="mb-2 justify-center text-[15px] font-semibold leading-5 sm:justify-start" />
          <p className="text-sm leading-6 text-muted-foreground">
            A local Python tool for turning public web pages into Markdown your
            agents can read, cite, and reuse.
          </p>
        </div>

        <div className="flex flex-col items-center gap-4 text-[15px] font-medium leading-5 text-muted-foreground sm:hidden">
          <div className="flex items-center gap-4">
            <a
              href="https://pypi.org/project/docpull/"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 inline-flex items-center hover:text-foreground transition-colors"
            >
              PyPI
            </a>
            <a
              href="/docs"
              className="min-h-11 inline-flex items-center hover:text-foreground transition-colors"
            >
              Docs
            </a>
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/docs/alternatives.md"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 inline-flex items-center hover:text-foreground transition-colors"
            >
              Compare
            </a>
            <a
              href="https://github.com/raintree-technology/docpull"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
              aria-label="GitHub repository"
            >
              <GithubIcon className="h-4 w-4" />
            </a>
            <a
              href="https://x.com/raintree_tech"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
              aria-label="X profile"
            >
              <XIcon className="h-4 w-4" />
            </a>
            <a
              href="https://raintree.technology"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
              aria-label="Raintree Technology"
            >
              <RaintreeLogo size={22} className="-mt-0.5 -ml-0.5" />
            </a>
          </div>
          <span className="text-sm">
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/LICENSE"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 inline-flex items-center hover:text-foreground transition-colors"
            >
              MIT
            </a>
          </span>
        </div>

        <div className="hidden sm:flex sm:items-center sm:justify-between">
          <div className="flex items-center gap-6 text-[15px] font-medium leading-5 text-muted-foreground">
            <a
              href="https://pypi.org/project/docpull/"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
            >
              PyPI
            </a>
            <a
              href="/docs"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
            >
              Docs
            </a>
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
            >
              Changelog
            </a>
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/docs/alternatives.md"
              target="_blank"
              rel="noopener noreferrer"
              className="min-h-11 min-w-11 inline-flex items-center justify-center hover:text-foreground transition-colors"
            >
              Compare
            </a>
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/LICENSE"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              MIT
            </a>
          </div>

          <div className="flex items-center gap-3 text-[15px] text-muted-foreground">
            <a
              href="https://x.com/raintree_tech"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
              aria-label="X"
            >
              <XIcon className="h-4 w-4" />
            </a>
            <a
              href="https://github.com/raintree-technology"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
              aria-label="GitHub"
            >
              <GithubIcon className="h-4 w-4" />
            </a>
            <a
              href="https://raintree.technology"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
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
