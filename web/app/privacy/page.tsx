import type { Metadata } from "next";
import { PageShell } from "../../components/SiteChrome";

const updatedAt = "July 6, 2026";

export const metadata: Metadata = {
  title: "Privacy",
  description:
    "Privacy notes for DocPull, a local-first open-source CLI, SDK, and MCP server.",
  alternates: {
    canonical: "/privacy",
  },
};

export default function PrivacyPage() {
  return (
    <PageShell>
      <main className="page-wrap">
        <section className="hero">
          <h1>Privacy</h1>
          <p>
            DocPull is designed as local-first software. The open-source package
            runs in your environment and does not require a DocPull account,
            hosted workspace, or telemetry service to fetch and export public
            web context.
          </p>
        </section>

        <div className="content-layout">
          <div>
            <section className="content-section">
              <h2>What DocPull collects</h2>
              <p>
                The DocPull CLI, SDK, and MCP server do not send product
                telemetry to Raintree Technology by default. Local commands may
                write caches, lockfiles, manifests, reports, and exported
                context packs on your machine or in paths you choose.
              </p>
            </section>

            <section className="content-section">
              <h2>Public sources you fetch</h2>
              <p>
                When you run DocPull against a URL, your environment connects to
                that public source. The source owner, hosting provider, network
                operator, or any proxy you configure may receive normal request
                metadata such as IP address, user agent, timestamps, and
                requested URLs.
              </p>
            </section>

            <section className="content-section">
              <h2>Optional providers and rendering</h2>
              <p>
                Some workflows can explicitly use third-party APIs, browser
                rendering, or cloud sandbox providers. Those integrations are
                user-configured and are governed by the third party&apos;s own
                privacy and data-processing terms.
              </p>
            </section>

            <section className="content-section">
              <h2>Repository and package services</h2>
              <p>
                If you visit GitHub, PyPI, package registries, directory
                listings, or linked documentation from this site, those services
                may process your visit according to their own policies.
              </p>
            </section>

            <section className="content-section">
              <h2>Contact</h2>
              <p>
                For privacy questions about DocPull, open a GitHub issue at{" "}
                <a href="https://github.com/raintree-technology/docpull/issues">
                  github.com/raintree-technology/docpull
                </a>{" "}
                or contact Raintree Technology through{" "}
                <a href="https://raintree.technology">raintree.technology</a>.
              </p>
            </section>
          </div>

          <aside className="sidebar">
            <h2>Last updated</h2>
            <p>{updatedAt}</p>
            <a href="/pricing">Pricing</a>
            <a href="/terms">Terms</a>
          </aside>
        </div>
      </main>
    </PageShell>
  );
}
