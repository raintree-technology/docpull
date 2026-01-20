"use client";

import dynamic from "next/dynamic";
import Header from "@/components/Header";
import Hero from "@/components/Hero";
import Footer from "@/components/Footer";
import Features from "@/components/Features";
import Profiles from "@/components/Profiles";
import CodeExamples from "@/components/CodeExamples";
import Install from "@/components/Install";

const AsciiBackground = dynamic(() => import("@/components/AsciiBackground"), {
  ssr: false,
});

export default function Home() {
  return (
    <>
      <AsciiBackground />
      <Header />
      <main className="relative z-10">
        <Hero />
        <Features />
        <Profiles />
        <CodeExamples />
        <Install />
      </main>
      <Footer />
    </>
  );
}
