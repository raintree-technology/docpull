import type { Metadata, Viewport } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";
import { site } from "@/lib/site";

export const metadata: Metadata = {
  metadataBase: new URL(site.baseUrl),
  title: {
    default: "docpull - Turn the web into Markdown.",
    template: "%s - docpull",
  },
  description: site.description,
  applicationName: "docpull",
  authors: [{ name: "Raintree Technology", url: "https://raintree.technology" }],
  creator: "Raintree Technology",
  publisher: "Raintree Technology",
  alternates: {
    canonical: "/",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  icons: {
    icon: "/logo.svg",
  },
  keywords: [
    "markdown",
    "web scraping",
    "web crawling",
    "website archiving",
    "python",
    "cli",
    "fetcher",
    "knowledge base",
    "server-rendered sites",
  ],

  openGraph: {
    title: "docpull - Turn the web into Markdown.",
    description:
      "Turn server-rendered sites into clean local Markdown with caching, deduplication, and strict network guards.",
    url: site.baseUrl,
    type: "website",
    siteName: "docpull",
    locale: "en_US",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "docpull - Turn the web into Markdown.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "docpull - Turn the web into Markdown.",
    description:
      "Turn server-rendered sites into clean local Markdown with caching, deduplication, and strict network guards.",
    site: "@raintree_tech",
    creator: "@raintree_tech",
    images: ["/og-image.png"],
  },
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0c11" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="scroll-smooth" suppressHydrationWarning>
      <body className="antialiased">
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>
        <ThemeProvider>{children}</ThemeProvider>
        <Analytics />
      </body>
    </html>
  );
}
