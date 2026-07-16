import type { Metadata } from "next";
import { PageShell } from "../../components/SiteChrome";

export const metadata: Metadata = {
  title: "Pricing",
  description:
    "DocPull pricing for the open-source Python CLI, SDK, and MCP server.",
  alternates: {
    canonical: "/pricing",
  },
};

export default function PricingPage() {
  return (
    <PageShell>
      <main className="page-wrap">
        <section className="hero">
          <h1>Pricing</h1>
          <p>
            DocPull is local-first open-source software. The core CLI, SDK, MCP
            server, cache, lockfile, diff, and export workflow are free to use
            under the MIT license.
          </p>
        </section>

        <section className="panel-grid" aria-label="DocPull pricing options">
          <article className="plan featured">
            <h2>Open source</h2>
            <div className="price">
              $0 <span>/ seat</span>
            </div>
            <p>For developers and teams running DocPull in their own environment.</p>
            <ul className="feature-list">
              <li>Python CLI and SDK</li>
              <li>MCP server for local clients</li>
              <li>Markdown, NDJSON, SQLite, and context-pack exports</li>
              <li>Local cache, lockfiles, diffs, and CI checks</li>
              <li>MIT license for personal and commercial use</li>
            </ul>
          </article>

          <article className="plan">
            <h2>Bring your own provider</h2>
            <div className="price">
              $0 <span>to DocPull</span>
            </div>
            <p>
              Optional API providers, browser rendering, or cloud sandboxes are
              billed by the provider you choose, not by DocPull.
            </p>
            <ul className="feature-list">
              <li>Explicit opt-in for paid-capable routes</li>
              <li>
                Use <code>--budget 0</code> to block paid-capable provider paths
              </li>
              <li>No DocPull account or metered hosted service required</li>
            </ul>
          </article>

          <article className="plan">
            <h2>Commercial support</h2>
            <div className="price">
              Contact <span>/ project</span>
            </div>
            <p>
              Support, integration help, and managed workflows may be offered
              separately for teams that need them.
            </p>
            <ul className="feature-list">
              <li>Context-dependency setup reviews</li>
              <li>CI and workflow integration help</li>
              <li>Custom source-pack or export guidance</li>
            </ul>
          </article>
        </section>

        <aside className="notice" aria-label="Pricing note">
          <p>
            DocPull does not currently sell a hosted SaaS plan from this site.
            If that changes, this page will be updated before any hosted billing
            is offered.
          </p>
        </aside>
      </main>
    </PageShell>
  );
}
