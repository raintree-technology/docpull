"use client";

import { Github } from "lucide-react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faXTwitter } from "@fortawesome/free-brands-svg-icons";

export default function Footer() {
  return (
    <footer className="border-t py-8 sm:py-6 relative z-10 bg-background">
      <div className="mx-auto max-w-5xl px-6">
        {/* Mobile: simple centered layout */}
        <div className="flex flex-col items-center gap-4 text-sm text-muted-foreground sm:hidden">
          <div className="flex items-center gap-4">
            <a
              href="https://pypi.org/project/docpull/"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              PyPI
            </a>
            <a
              href="https://github.com/raintree-technology/docpull#readme"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              Docs
            </a>
            <a
              href="https://github.com/raintree-technology/docpull"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              <Github className="h-4 w-4" />
            </a>
            <a
              href="https://x.com/raintree_tech"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              <FontAwesomeIcon icon={faXTwitter} className="h-4 w-4" />
            </a>
          </div>
          <span className="text-xs">
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/LICENSE"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              MIT
            </a>
            {" · "}
            Raintree Technology
          </span>
        </div>

        {/* Desktop: full layout */}
        <div className="hidden sm:flex sm:items-center sm:justify-between">
          <div className="flex items-center gap-6 text-sm text-muted-foreground">
            <a
              href="https://pypi.org/project/docpull/"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              PyPI
            </a>
            <a
              href="https://github.com/raintree-technology/docpull#readme"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              Docs
            </a>
            <a
              href="https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              Changelog
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

          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <a
              href="https://raintree.technology"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
            >
              Raintree Technology
            </a>
            <a
              href="https://x.com/raintree_tech"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
              aria-label="X"
            >
              <FontAwesomeIcon icon={faXTwitter} className="h-4 w-4" />
            </a>
            <a
              href="https://github.com/raintree-technology"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-foreground transition-colors"
              aria-label="GitHub"
            >
              <Github className="h-4 w-4" />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
