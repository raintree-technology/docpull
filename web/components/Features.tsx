import { featuredFeatures, supportingFeatures } from "@/lib/content/home";

export default function Features() {
  return (
    <section
      id="features"
      className="border-t border-foreground/8 pt-16 pb-24 sm:pt-24"
    >
      <div className="mx-auto max-w-6xl px-6">
        <div className="mb-12 max-w-3xl text-center sm:text-left">
          <p className="section-kicker mb-3">What matters</p>
          <h2 className="section-title mb-4">
            The core behaviors carry the page.
          </h2>
          <p className="section-copy">
            The product is strongest when it is concrete: clean Markdown, early
            dedup, and strict network behavior. Those should read like the core
            of the page, not six identical cards.
          </p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 sm:gap-6">
          {featuredFeatures.map((feature) => (
            <article
              key={feature.title}
              className={`apple-panel rounded-[1.75rem] p-5 sm:p-7 ${feature.className}`}
            >
              <div className="flex items-center gap-2 mb-4">
                <span className="inline-block h-2 w-2 rounded-full bg-foreground/80" />
                <span className="text-[11px] font-mono uppercase tracking-[0.16em] text-foreground/55">
                  Core behavior
                </span>
              </div>
              <h3 className="text-xl sm:text-2xl font-medium tracking-tight text-foreground mb-3">
                {feature.title}
              </h3>
              <p className="text-sm sm:text-base leading-relaxed text-foreground/78 mb-5">
                {feature.description}
              </p>

              <div className="mb-5 overflow-x-auto rounded-[1.25rem] border border-foreground/10 bg-background/55 p-4">
                <pre className="text-xs sm:text-sm text-foreground/88">
                  <code className="whitespace-pre">{feature.snippet}</code>
                </pre>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {feature.points.map((point) => (
                  <div
                    key={point}
                    className="rounded-2xl border border-foreground/10 bg-foreground/[0.03] px-3.5 py-3 text-sm text-foreground/72"
                  >
                    {point}
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>

        <div className="mt-5 rounded-[1.75rem] apple-panel p-5 sm:mt-6 sm:p-6">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between mb-5">
            <div>
              <h3 className="text-lg sm:text-xl font-medium tracking-tight text-foreground">
                The supporting details still matter
              </h3>
              <p className="text-sm sm:text-base text-foreground/72 leading-relaxed">
                These are important, but they read better as a tighter list than
                as another wall of equal-weight cards.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {supportingFeatures.map((feature) => (
              <article
                key={feature.title}
                className="rounded-[1.25rem] border border-foreground/10 bg-foreground/[0.025] px-4 py-4"
              >
                <h4 className="text-sm font-medium text-foreground mb-2">
                  {feature.title}
                </h4>
                <p className="text-sm leading-relaxed text-foreground/68">
                  {feature.description}
                </p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
