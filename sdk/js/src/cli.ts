/**
 * Helpers for running the docpull CLI from JavaScript.
 *
 * Commands are spawned with an argument array and `shell: false`, so no
 * shell interpolation happens. A non-zero exit rejects with the captured
 * stderr in the error.
 */

import { spawn } from "node:child_process";

export interface SpawnedProcessStream {
  on(event: "data", listener: (chunk: unknown) => void): void;
}

/** Minimal child process surface used by `runDocpull`; injectable in tests. */
export interface SpawnedProcess {
  stdout: SpawnedProcessStream | null;
  stderr: SpawnedProcessStream | null;
  on(event: "error", listener: (error: Error) => void): void;
  on(event: "close", listener: (code: number | null) => void): void;
}

export interface SpawnCallOptions {
  cwd?: string;
  env?: Record<string, string | undefined>;
  shell: false;
}

export type SpawnFunction = (
  command: string,
  args: readonly string[],
  options: SpawnCallOptions,
) => SpawnedProcess;

export interface RunDocpullOptions {
  /** Executable to run. Defaults to `docpull` resolved from PATH. */
  bin?: string;
  cwd?: string;
  env?: Record<string, string | undefined>;
  /** Spawn implementation override, used by tests to avoid real processes. */
  spawnFn?: SpawnFunction;
}

export interface RunDocpullResult {
  command: string;
  args: string[];
  stdout: string;
  stderr: string;
  exitCode: number;
}

export class DocpullCliError extends Error {
  readonly exitCode: number | null;
  readonly stderr: string;

  constructor(message: string, exitCode: number | null, stderr: string) {
    super(message);
    this.name = "DocpullCliError";
    this.exitCode = exitCode;
    this.stderr = stderr;
  }
}

const defaultSpawn: SpawnFunction = (command, args, options) => {
  const child = spawn(command, [...args], {
    cwd: options.cwd,
    env: options.env,
    shell: false,
  });
  return {
    stdout: child.stdout,
    stderr: child.stderr,
    on(event: "error" | "close", listener: ((error: Error) => void) | ((code: number | null) => void)) {
      child.on(event, listener);
    },
  };
};

/** Run the docpull CLI with the given arguments and capture its output. */
export function runDocpull(args: readonly string[], options: RunDocpullOptions = {}): Promise<RunDocpullResult> {
  const command = options.bin ?? "docpull";
  const spawnFn = options.spawnFn ?? defaultSpawn;
  const argv = [...args];
  return new Promise((resolve, reject) => {
    const child = spawnFn(command, argv, { cwd: options.cwd, env: options.env, shell: false });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("error", (error) => {
      reject(new DocpullCliError(`Failed to run ${command}: ${error.message}`, null, stderr));
    });
    child.on("close", (code) => {
      if (code === 0) {
        resolve({ command, args: argv, stdout, stderr, exitCode: 0 });
        return;
      }
      const detail = stderr.trim();
      reject(
        new DocpullCliError(
          `${command} exited with code ${String(code)}${detail ? `: ${detail}` : ""}`,
          code,
          stderr,
        ),
      );
    });
  });
}

export interface FetchToPackOptions extends RunDocpullOptions {
  /** Value for `--budget`; pass 0 to disable the network budget. */
  budget?: number;
  /** Extra CLI arguments appended after the generated ones. */
  extraArgs?: readonly string[];
}

/** Fetch a URL into a pack directory: `docpull <url> -o <outputDir>`. */
export function fetchToPack(
  url: string,
  outputDir: string,
  options: FetchToPackOptions = {},
): Promise<RunDocpullResult> {
  if (url.trim().length === 0) {
    throw new Error("fetchToPack requires a non-empty url");
  }
  if (outputDir.trim().length === 0) {
    throw new Error("fetchToPack requires a non-empty outputDir");
  }
  const args: string[] = [url, "-o", outputDir];
  if (options.budget !== undefined) {
    args.push("--budget", String(options.budget));
  }
  if (options.extraArgs !== undefined) {
    args.push(...options.extraArgs);
  }
  return runDocpull(args, options);
}
