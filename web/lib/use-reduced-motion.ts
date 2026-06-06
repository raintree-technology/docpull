"use client";

import { useSyncExternalStore } from "react";

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function getSnapshot() {
  return window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

function getServerSnapshot() {
  return false;
}

function subscribe(callback: () => void) {
  const query = window.matchMedia(REDUCED_MOTION_QUERY);

  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}

export function useReducedMotion() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
