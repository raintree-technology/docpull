import { faqs } from "@/lib/content/faqs";
import { site } from "@/lib/site";

// Spec: SEO / Structured data (JSON-LD). A single @graph describing the site,
// the publishing organization, the software itself, and the FAQ. Rendered
// server-side so crawlers and AI agents see it in the initial HTML.

const graph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${site.baseUrl}/#organization`,
      name: "Raintree Technology",
      url: "https://raintree.technology",
      sameAs: [
        "https://github.com/raintree-technology",
        "https://x.com/raintree_tech",
      ],
    },
    {
      "@type": "WebSite",
      "@id": `${site.baseUrl}/#website`,
      url: site.baseUrl,
      name: "docpull",
      description: site.description,
      inLanguage: "en",
      publisher: { "@id": `${site.baseUrl}/#organization` },
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${site.baseUrl}/#software`,
      name: "docpull",
      applicationCategory: "DeveloperApplication",
      operatingSystem: "macOS, Linux, Windows",
      url: site.baseUrl,
      downloadUrl: "https://pypi.org/project/docpull/",
      softwareHelp: "https://github.com/raintree-technology/docpull#readme",
      description:
        "Browser-free web puller for turning server-rendered sites into clean Markdown with caching, deduplication, and strict network guards.",
      author: { "@id": `${site.baseUrl}/#organization` },
      license: "https://opensource.org/licenses/MIT",
      isAccessibleForFree: true,
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
      },
    },
    {
      "@type": "FAQPage",
      "@id": `${site.baseUrl}/#faq`,
      mainEntity: faqs.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: {
          "@type": "Answer",
          text: f.aText,
        },
      })),
    },
  ],
};

export default function StructuredData() {
  return (
    <script
      type="application/ld+json"
      // Escape "<" so the payload can never break out of the <script> element.
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(graph).replace(/</g, "\\u003c"),
      }}
    />
  );
}
