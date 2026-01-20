import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/ThemeProvider";

export const metadata: Metadata = {
  title: "docpull - Fetch docs. Get AI-ready Markdown.",
  description:
    "Fast, type-safe, secure documentation fetcher. Transform any docs site into clean, AI-ready Markdown for LLMs, RAG pipelines, and offline archives.",
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
    title: "docpull - Fetch docs. Get AI-ready Markdown.",
    description:
      "Fast, type-safe, secure documentation fetcher for LLMs and RAG pipelines.",
    type: "website",
    siteName: "docpull",
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
    title: "docpull - Fetch docs. Get AI-ready Markdown.",
    description:
      "Fast, type-safe, secure documentation fetcher for LLMs and RAG pipelines.",
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
      </body>
    </html>
  );
}
