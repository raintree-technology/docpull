"use client";

import { useEffect, useRef } from "react";

const ASCII_CHARS = " .·:;+*#%@";
const TARGET_FPS = 24;
const FRAME_INTERVAL = 1000 / TARGET_FPS;

export default function AsciiBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const timeRef = useRef(0);
  const animationRef = useRef<number>(0);
  const lastFrameRef = useRef(0);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Larger chars = fewer to render
    const charWidth = 14;
    const charHeight = 20;

    const resize = () => {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    };

    const animate = (timestamp: number) => {
      animationRef.current = requestAnimationFrame(animate);

      // Throttle to target FPS
      const elapsed = timestamp - lastFrameRef.current;
      if (elapsed < FRAME_INTERVAL) return;
      lastFrameRef.current = timestamp - (elapsed % FRAME_INTERVAL);

      timeRef.current += 0.035;
      const t = timeRef.current;

      const cols = Math.ceil(canvas.width / charWidth);
      const rows = Math.ceil(canvas.height / charHeight);
      const centerX = cols / 2;
      const centerY = rows / 2;

      const isDark = document.documentElement.classList.contains("dark");

      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.font = `${charHeight - 2}px "SF Mono", "Fira Code", Consolas, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      const charsLength = ASCII_CHARS.length;
      const fillLight = isDark ? "255, 255, 255" : "0, 0, 0";

      for (let y = 0; y < rows; y++) {
        for (let x = 0; x < cols; x++) {
          const dx = (x - centerX) * 0.6;
          const dy = (y - centerY) * 1.0;
          const dist = Math.sqrt(dx * dx + dy * dy);

          const angle = Math.atan2(dy, dx);
          const rotation = t * 0.25;

          const spiralValue = Math.sin(angle * 4 + dist * 0.12 - rotation);
          const wave1 =
            Math.sin(x * 0.05 + t * 0.5) * Math.cos(y * 0.04 + t * 0.4);
          const wave2 = Math.sin(dist * 0.08 - t * 0.35);

          const combined = spiralValue * 0.4 + 0.5 + wave1 * 0.2 + wave2 * 0.15;

          const charIndex = Math.floor(
            Math.max(0, Math.min(0.99, combined)) * charsLength,
          );
          const char = ASCII_CHARS[charIndex];

          const opacity = 0.04 + combined * 0.12;
          const clampedOpacity = Math.max(0.03, Math.min(0.18, opacity));

          ctx.fillStyle = `rgba(${fillLight}, ${clampedOpacity})`;
          ctx.fillText(
            char,
            x * charWidth + charWidth / 2,
            y * charHeight + charHeight / 2,
          );
        }
      }
    };

    resize();
    animationRef.current = requestAnimationFrame(animate);

    window.addEventListener("resize", resize);

    return () => {
      cancelAnimationFrame(animationRef.current);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-0"
      aria-hidden="true"
    />
  );
}
