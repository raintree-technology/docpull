import AsciiBackgroundLoader from "@/components/AsciiBackgroundLoader";
import Header from "@/components/Header";
import Hero from "@/components/Hero";
import Footer from "@/components/Footer";
import Features from "@/components/Features";
import HowItWorks from "@/components/HowItWorks";
import Profiles from "@/components/Profiles";
import CodeExamples from "@/components/CodeExamples";
import Install from "@/components/Install";
import FAQ from "@/components/FAQ";
import StructuredData from "@/components/StructuredData";

export default function Home() {
  return (
    <>
      <StructuredData />
      <AsciiBackgroundLoader />
      <Header />
      <main className="relative z-10">
        <Hero />
        <HowItWorks />
        <Features />
        <Profiles />
        <CodeExamples />
        <Install />
        <FAQ />
      </main>
      <Footer />
    </>
  );
}
