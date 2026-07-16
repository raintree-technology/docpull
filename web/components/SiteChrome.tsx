import Link from "next/link";
import { LogoMark } from "./LogoMark";

export function SiteHeader() {
  return (
    <header className="site-header">
      <div className="site-bar">
        <Link className="brand" href="/">
          <LogoMark className="brand-mark" />
          <span>DocPull</span>
        </Link>
        <nav className="site-nav" aria-label="Primary navigation">
          <Link href="/pricing">Pricing</Link>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <a href="https://github.com/raintree-technology/docpull">GitHub</a>
        </nav>
      </div>
    </header>
  );
}

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <span>Raintree Technology. DocPull is released under the MIT license.</span>
        <nav className="footer-links" aria-label="Footer navigation">
          <a href="https://github.com/raintree-technology/docpull">GitHub</a>
          <a href="https://pypi.org/project/docpull/">PyPI</a>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
        </nav>
      </div>
    </footer>
  );
}

export function PageShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="site-shell">
      <SiteHeader />
      {children}
      <SiteFooter />
    </div>
  );
}
