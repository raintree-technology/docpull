"use client";

import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { GithubIcon } from "./GithubIcon";

export default function Header() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 20);
    handleScroll();
    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <header
      className={cn(
        "fixed top-0 left-0 right-0 z-50 transition-all duration-200",
        scrolled
          ? "border-b bg-background/72 backdrop-blur-xl"
          : "bg-transparent",
      )}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        <a
          href="#overview"
          className="flex min-h-11 items-center gap-2 rounded-full px-1 text-sm font-medium"
        >
          <svg
            width="18"
            height="18"
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
        </a>

        <nav
          aria-label="Primary"
          className="hidden items-center gap-1 rounded-full border border-foreground/8 bg-background/55 px-2 py-1 backdrop-blur md:flex"
        >
          <a
            href="#overview"
            className="rounded-full px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Overview
          </a>
          <a
            href="#how-it-works"
            className="rounded-full px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            How It Works
          </a>
          <a
            href="#profiles"
            className="rounded-full px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Profiles
          </a>
          <a
            href="#mcp"
            className="rounded-full px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Clients
          </a>
          <a
            href="#install"
            className="rounded-full px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            Install
          </a>
        </nav>

        <div className="flex items-center gap-1">
          <a href="#install" className="apple-button hidden md:inline-flex">
            Get Started
          </a>
          <a
            href="https://github.com/raintree-technology/docpull"
            target="_blank"
            rel="noopener noreferrer"
            className="flex h-11 w-11 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
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
