"use client";

import dynamic from "next/dynamic";

const AsciiBackground = dynamic(() => import("./AsciiBackground"), {
  ssr: false,
});

export default function AsciiBackgroundLoader() {
  return <AsciiBackground />;
}
