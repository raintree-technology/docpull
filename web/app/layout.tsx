import type { Metadata } from "next";
import { Analytics } from "@vercel/analytics/next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";

const baseUrl = "https://docpull.raintree.technology";

export const metadata: Metadata = {
  metadataBase: new URL(baseUrl),
  title: "docpull - Fetch docs. Get AI-ready Markdown.",
  description:
    "Fast, type-safe, secure documentation fetcher. Transform any docs site into clean, AI-ready Markdown for LLMs, RAG pipelines, and offline archives.",
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
    "documentation",
    "markdown",
    "AI",
    "LLM",
    "RAG",
    "web scraping",
    "python",
    "cli",
    "docs",
    "fetcher",
  ],

  openGraph: {
    title: "docpull - Fetch docs. Get clean, AI-ready Markdown.",
    description:
      "Turn any documentation site into clean Markdown for LLMs, RAG pipelines, and training datasets. Fast, secure, and built for AI workflows.",
    url: baseUrl,
    type: "website",
    siteName: "docpull",
    locale: "en_US",
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "docpull - Fetch docs. Get clean Markdown.",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "docpull - Fetch docs. Get clean, AI-ready Markdown.",
    description:
      "Turn any documentation site into clean Markdown for LLMs, RAG pipelines, and training datasets. Fast, secure, and built for AI workflows.",
    site: "@raintree_tech",
    creator: "@raintree_tech",
    images: ["/og-image.png"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="scroll-smooth" suppressHydrationWarning>
      <body className="antialiased">
        <ThemeProvider>{children}</ThemeProvider>
        <Analytics />
      </body>
    </html>
  );
}
