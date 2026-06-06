"use client";

import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { GithubIcon } from "./GithubIcon";

export default function Header() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={cn(
        "fixed top-0 left-0 right-0 z-50 transition-all duration-200",
        scrolled
          ? "bg-background/80 backdrop-blur-xs border-b"
          : "bg-transparent",
      )}
    >
      <div className="flex h-14 items-center justify-between px-6 max-w-5xl mx-auto">
        <a
          href="#"
          className="font-medium text-sm min-h-11 flex items-center gap-2"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 32 32"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
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
        </a>

        <nav className="hidden md:flex items-center gap-6">
          <a
            href="#features"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Features
          </a>
          <a
            href="#examples"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Examples
          </a>
          <a
            href="#install"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            Install
          </a>
        </nav>

        <div className="flex items-center gap-1">
          <a
            href="https://github.com/raintree-technology/docpull"
            target="_blank"
            rel="noopener noreferrer"
            className="w-11 h-11 flex items-center justify-center rounded-md text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
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
