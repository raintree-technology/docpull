import { absoluteUrl, discoveryPaths, site, utcDate } from "@/lib/site";
import { escapeXml } from "@/lib/utils";

export const dynamic = "force-static";

export function GET() {
  const published = utcDate(site.publishedAt);
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>${escapeXml(site.name)}</title>
    <link>${absoluteUrl(discoveryPaths.home)}</link>
    <description>${escapeXml(site.rssDescription)}</description>
    <language>en-us</language>
    <lastBuildDate>${published}</lastBuildDate>
    <item>
      <title>${escapeXml("docpull homepage")}</title>
      <link>${absoluteUrl(discoveryPaths.home)}</link>
      <guid isPermaLink="true">${absoluteUrl(discoveryPaths.home)}</guid>
      <pubDate>${published}</pubDate>
      <description>${escapeXml(
        "Product overview, setup instructions, examples, and agent-readiness links.",
      )}</description>
    </item>
  </channel>
</rss>`;

  return new Response(xml, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
