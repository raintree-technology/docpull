import { PageShell } from "../components/SiteChrome";

export default function HomePage() {
  return (
    <PageShell>
      <main className="page-wrap">
        <section className="hero">
          <h1>Local-first context dependencies for AI agents.</h1>
          <p>
            DocPull is a Python CLI, SDK, and MCP server for turning public web
            sources into cited context packs that can be synced, diffed, and
            exported without a hosted account.
          </p>
          <div className="actions">
            <a
              className="button primary"
              href="https://github.com/raintree-technology/docpull#readme"
            >
              Read the docs
            </a>
            <a className="button" href="https://pypi.org/project/docpull/">
              Install from PyPI
            </a>
          </div>
        </section>

        <section className="panel-grid" aria-label="DocPull basics">
          <article className="panel">
            <h2>Open source</h2>
            <p>
              The core package is MIT licensed and runs locally from your
              environment.
            </p>
          </article>
          <article className="panel">
            <h2>No account required</h2>
            <p>
              Fetch, cache, diff, and export public sources without creating a
              DocPull account.
            </p>
          </article>
          <article className="panel">
            <h2>Explicit paid routes</h2>
            <p>
              Optional provider or cloud-rendering paths remain user-controlled
              and budget gated.
            </p>
          </article>
        </section>
      </main>
    </PageShell>
  );
}
