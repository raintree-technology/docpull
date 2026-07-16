import type { Metadata } from "next";
import { PageShell } from "../../components/SiteChrome";

const updatedAt = "July 6, 2026";

export const metadata: Metadata = {
  title: "Terms",
  description:
    "Terms for using the DocPull website and open-source project resources.",
  alternates: {
    canonical: "/terms",
  },
};

export default function TermsPage() {
  return (
    <PageShell>
      <main className="page-wrap">
        <section className="hero">
          <h1>Terms</h1>
          <p>
            These terms cover the DocPull website and public project resources.
            The DocPull source code is distributed under the MIT license in the
            GitHub repository.
          </p>
        </section>

        <div className="content-layout">
          <div>
            <section className="content-section">
              <h2>Open-source license</h2>
              <p>
                Use of the DocPull code is governed by the MIT license included
                in the repository. If these website terms and the repository
                license differ for source-code use, the repository license
                controls.
              </p>
            </section>

            <section className="content-section">
              <h2>Acceptable use</h2>
              <p>
                Use DocPull only for sources you are allowed to access. Respect
                applicable law, robots.txt, site terms, rate limits, credentials,
                and third-party service policies.
              </p>
            </section>

            <section className="content-section">
              <h2>No hosted-service warranty</h2>
              <p>
                DocPull is provided as open-source software and project
                documentation. The website does not currently provide a hosted
                DocPull processing service or account workspace.
              </p>
            </section>

            <section className="content-section">
              <h2>Third-party services</h2>
              <p>
                Optional provider APIs, package registries, GitHub, PyPI, and
                linked services are operated by their respective providers. You
                are responsible for any credentials, costs, and terms that apply
                when you choose to use them.
              </p>
            </section>

            <section className="content-section">
              <h2>Changes</h2>
              <p>
                These terms may be updated as DocPull&apos;s public surfaces
                change. Material updates will be reflected by the date on this
                page.
              </p>
            </section>
          </div>

          <aside className="sidebar">
            <h2>Last updated</h2>
            <p>{updatedAt}</p>
            <a href="/privacy">Privacy</a>
            <a href="/pricing">Pricing</a>
          </aside>
        </div>
      </main>
    </PageShell>
  );
}
