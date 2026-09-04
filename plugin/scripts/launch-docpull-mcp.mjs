#!/usr/bin/env node

import { spawn } from "node:child_process";

const child = spawn("docpull", ["mcp", ...process.argv.slice(2)], {
  stdio: "inherit",
});

child.on("error", (error) => {
  if (error.code === "ENOENT") {
    console.error(
      "DocPull is not installed. Run: pipx install 'docpull[mcp]==6.5.3'",
    );
    process.exitCode = 127;
    return;
  }

  console.error(`DocPull could not start: ${error.message}`);
  process.exitCode = 1;
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }

  process.exitCode = code ?? 1;
});
