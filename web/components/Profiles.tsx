import { profiles } from "@/lib/content/home";

const featuredProfile = profiles[3];
const standardProfiles = profiles.slice(0, 3);

export default function Profiles() {
  return (
    <section id="profiles" className="py-16 sm:py-24">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 max-w-3xl text-center sm:text-left">
          <p className="section-kicker mb-3">Profiles with opinions</p>
          <h2 className="section-title mb-4">
            Start from the profile that matches the job.
          </h2>
          <p className="section-copy">
            These are not cosmetic presets. Each profile makes tradeoffs around
            depth, output shape, and failure behavior so you do not have to
            rebuild them from flags every time.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] gap-4 sm:gap-6">
          <article className="apple-panel rounded-[1.75rem] p-5 sm:p-6">
            <div className="flex items-center justify-between gap-4 mb-4">
              <div>
                <p className="text-[11px] font-mono uppercase tracking-[0.18em] text-foreground/55">
                  Featured Profile
                </p>
                <h3 className="text-2xl sm:text-3xl font-medium tracking-tight text-foreground mt-2">
                  {featuredProfile.name}
                </h3>
              </div>
              <span className="rounded-full border border-foreground/12 bg-foreground/[0.04] px-3 py-1 text-[11px] font-mono uppercase tracking-[0.14em] text-foreground/62">
                For pipelines
              </span>
            </div>

            <p className="text-base sm:text-lg text-foreground/78 leading-relaxed mb-5">
              {featuredProfile.description}
            </p>

            <div className="mb-5 rounded-[1.25rem] border border-foreground/10 bg-background/55 p-4">
              <code className="block text-xs sm:text-sm font-mono text-foreground/80 overflow-x-auto">
                {featuredProfile.example}
              </code>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="rounded-[1.25rem] border border-foreground/10 bg-foreground/[0.03] px-4 py-3">
                <p className="text-[10px] font-mono uppercase tracking-[0.16em] text-foreground/50 mb-2">
                  Output shape
                </p>
                <p className="text-sm text-foreground/72 leading-relaxed">
                  NDJSON chunks sized for model ingestion instead of one file
                  per page.
                </p>
              </div>
              <div className="rounded-[1.25rem] border border-foreground/10 bg-foreground/[0.03] px-4 py-3">
                <p className="text-[10px] font-mono uppercase tracking-[0.16em] text-foreground/50 mb-2">
                  Failure mode
                </p>
                <p className="text-sm text-foreground/72 leading-relaxed">
                  JS-only pages are skipped with a clear reason; add
                  --strict-js-required when they should fail instead.
                </p>
              </div>
            </div>
          </article>

          <div className="grid grid-cols-1 gap-3">
            {standardProfiles.map((profile, index) => (
              <article key={profile.name} className="apple-panel rounded-[1.5rem] p-4 sm:p-5">
                <div className="flex items-start justify-between gap-4 mb-3">
                  <div>
                    <p className="text-[10px] font-mono uppercase tracking-[0.16em] text-foreground/50 mb-2">
                      Profile {String(index + 1).padStart(2, "0")}
                    </p>
                    <h3 className="text-lg font-medium tracking-tight text-foreground">
                      {profile.name}
                    </h3>
                  </div>
                  <span className="text-[11px] text-foreground/55 text-right max-w-[11rem] leading-relaxed">
                    {profile.accent}
                  </span>
                </div>
                <p className="text-sm text-foreground/72 mb-3 leading-relaxed">
                  {profile.description}
                </p>
                <code className="block overflow-x-auto rounded-xl bg-background/60 px-3 py-2 text-xs font-mono text-foreground/70">
                  {profile.example}
                </code>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
