import { absoluteUrl, discoveryPaths, site } from "@/lib/site";

export const dynamic = "force-static";

const body = `Contact: mailto:support@raintree.technology
Expires: ${site.securityExpiresAt}
Preferred-Languages: en
Canonical: ${absoluteUrl(discoveryPaths.security)}
Acknowledgments: https://github.com/raintree-technology/docpull
`;

export function GET() {
  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
