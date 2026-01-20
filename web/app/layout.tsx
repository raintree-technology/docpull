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
  },
  twitter: {
    card: "summary_large_image",
    title: "docpull - Fetch docs. Get AI-ready Markdown.",
    description:
      "Fast, type-safe, secure documentation fetcher for LLMs and RAG pipelines.",
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
