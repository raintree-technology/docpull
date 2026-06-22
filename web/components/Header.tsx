"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { cn } from "@/lib/utils";
import { ThemeToggle } from "./ThemeToggle";
import { GithubIcon } from "./GithubIcon";
import { BrandMark } from "@/components/landing";

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
          ? "border-b bg-background/85 backdrop-blur-md"
          : "bg-transparent",
      )}
    >
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link
          href="/"
          className="flex min-h-11 items-center gap-2 text-[15px] font-semibold leading-5"
        >
          <BrandMark iconClassName="h-4.5 w-4.5" />
        </Link>

        <nav className="hidden md:flex items-center gap-5">
          <a
            href="#features"
            className="text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Features
          </a>
          <a
            href="#compare"
            className="text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Compare
          </a>
          <a
            href="/docs"
            className="text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Docs
          </a>
          <a
            href="#examples"
            className="text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
          >
            Examples
          </a>
          <a
            href="#install"
            className="text-[15px] font-medium leading-5 text-muted-foreground transition-colors hover:text-foreground"
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
