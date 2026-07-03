import { GlassPanel, LandingSection } from "@/components/landing";

type ComparisonRow = {
  layer: string;
  useFor: string;
  output: string;
  docpullBoundary: string;
};

const rows = [
  {
    layer: "DocPull",
    useFor:
      "Known public URLs, explicit source lists, lockfiles, local pack prep, validation, exports, and Context CI.",
    output:
      "Markdown, JSON, NDJSON, SQLite, OKF, v3 sidecars, citation indexes, audit reports, and agent exports.",
    docpullBoundary:
      "Owns the local artifact contract from source acquisition through pack validation.",
  },
  {
    layer: "Browser automation",
    useFor:
      "Interactive sessions, private apps, complex JavaScript flows, clicks, scrolling, and stateful workflows.",
    output:
      "Rendered HTML, screenshots, traces, or domain-specific captured data.",
    docpullBoundary:
      "Run it separately, then pass exported HTML or files into DocPull when you need pack artifacts.",
  },
  {
    layer: "Search or research APIs",
    useFor:
      "Finding candidate sources before you know which URLs should become context dependencies.",
    output:
      "Ranked URLs, snippets, summaries, extracted records, or research reports.",
    docpullBoundary:
      "Normalize selected URLs and records into a local v3 pack before agents depend on them.",
  },
  {
    layer: "Document parsers",
    useFor:
      "PDFs, office files, specs, datasets, transcripts, papers, packages, and local files that are not normal web pages.",
    output:
      "Text, structured records, extracted tables, or parsed document pages.",
    docpullBoundary:
      "Use `docpull parse`, `openapi-pack`, or typed pack lanes to make those inputs auditable alongside web sources.",
  },
] as const satisfies readonly ComparisonRow[];

export default function Comparison() {
  return (
    <LandingSection
      id="compare"
      title="Where DocPull Fits"
      description="DocPull is the local artifact layer. It turns selected sources into repeatable packs that agents can inspect, cite, diff, validate, and export."
      containerClassName="max-w-6xl"
      headerClassName="mb-10"
      bordered={false}
    >
      <GlassPanel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] border-collapse text-left">
            <caption className="sr-only">
              Comparison of DocPull, browser automation, search APIs, and
              document parsers across role, outputs, and boundaries.
            </caption>
            <thead>
              <tr className="border-b border-foreground/10">
                {["Layer", "Use for", "Typical output", "DocPull boundary"].map(
                  (heading) => (
                    <th
                      key={heading}
                      scope="col"
                      className="px-4 py-4 text-xs font-semibold uppercase leading-5 tracking-[0.12em] text-muted-foreground"
                    >
                      {heading}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.layer}
                  className="border-b border-foreground/10 last:border-b-0"
                >
                  <th
                    scope="row"
                    className="w-[170px] px-4 py-4 align-top text-sm font-semibold leading-6 text-foreground"
                  >
                    {row.layer}
                  </th>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.useFor}
                  </td>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.output}
                  </td>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.docpullBoundary}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="border-t border-foreground/10 px-4 py-4">
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            External tools can still be part of a workflow. The public DocPull
            contract starts when selected URLs, files, specs, or records are
            turned into local artifacts with stable IDs, rights state,
            provenance, citations, and validation results.
          </p>
        </div>
      </GlassPanel>
    </LandingSection>
  );
}
