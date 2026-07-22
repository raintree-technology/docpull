import { describe, expect, test } from "bun:test";

import {
  DocpullCliError,
  fetchToPack,
  runDocpull,
  type SpawnCallOptions,
  type SpawnedProcess,
  type SpawnFunction,
} from "./cli.js";

interface RecordedCall {
  command: string;
  args: string[];
  options: SpawnCallOptions;
}

interface StubBehavior {
  exitCode?: number;
  stdout?: string;
  stderr?: string;
}

function stubSpawn(calls: RecordedCall[], behavior: StubBehavior = {}): SpawnFunction {
  const { exitCode = 0, stdout = "", stderr = "" } = behavior;
  return (command, args, options) => {
    calls.push({ command, args: [...args], options });
    const child: SpawnedProcess = {
      stdout: {
        on(event, listener) {
          if (event === "data" && stdout.length > 0) {
            queueMicrotask(() => listener(stdout));
          }
        },
      },
      stderr: {
        on(event, listener) {
          if (event === "data" && stderr.length > 0) {
            queueMicrotask(() => listener(stderr));
          }
        },
      },
      on(event: "error" | "close", listener: ((error: Error) => void) | ((code: number | null) => void)) {
        if (event === "close") {
          setTimeout(() => (listener as (code: number | null) => void)(exitCode), 0);
        }
      },
    };
    return child;
  };
}

describe("runDocpull", () => {
  test("spawns docpull with verbatim args and no shell", async () => {
    const calls: RecordedCall[] = [];
    const result = await runDocpull(["pack", "validate", "./packs/example"], {
      spawnFn: stubSpawn(calls, { stdout: "ok\n" }),
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]?.command).toBe("docpull");
    expect(calls[0]?.args).toEqual(["pack", "validate", "./packs/example"]);
    expect(calls[0]?.options.shell).toBe(false);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toBe("ok\n");
  });

  test("honors a custom binary and cwd", async () => {
    const calls: RecordedCall[] = [];
    await runDocpull(["--version"], {
      bin: "/opt/tools/docpull",
      cwd: "/tmp/work",
      spawnFn: stubSpawn(calls),
    });

    expect(calls[0]?.command).toBe("/opt/tools/docpull");
    expect(calls[0]?.options.cwd).toBe("/tmp/work");
  });

  test("rejects on non-zero exit with stderr in the error", async () => {
    const calls: RecordedCall[] = [];
    const promise = runDocpull(["bad-arg"], {
      spawnFn: stubSpawn(calls, { exitCode: 2, stderr: "unknown argument\n" }),
    });

    await expect(promise).rejects.toThrow("exited with code 2");
    await promise.catch((error: unknown) => {
      expect(error).toBeInstanceOf(DocpullCliError);
      if (error instanceof DocpullCliError) {
        expect(error.exitCode).toBe(2);
        expect(error.stderr).toBe("unknown argument\n");
      }
    });
  });
});

describe("fetchToPack", () => {
  test("maps to `docpull <url> -o <dir>`", async () => {
    const calls: RecordedCall[] = [];
    await fetchToPack("https://example.com/docs", "./packs/example", {
      spawnFn: stubSpawn(calls),
    });

    expect(calls[0]?.command).toBe("docpull");
    expect(calls[0]?.args).toEqual(["https://example.com/docs", "-o", "./packs/example"]);
  });

  test("passes --budget through when provided", async () => {
    const calls: RecordedCall[] = [];
    await fetchToPack("https://example.com/docs", "./packs/example", {
      budget: 0,
      spawnFn: stubSpawn(calls),
    });

    expect(calls[0]?.args).toEqual(["https://example.com/docs", "-o", "./packs/example", "--budget", "0"]);
  });

  test("appends extraArgs after generated args", async () => {
    const calls: RecordedCall[] = [];
    await fetchToPack("https://example.com/docs", "./packs/example", {
      extraArgs: ["--format", "ndjson"],
      spawnFn: stubSpawn(calls),
    });

    expect(calls[0]?.args).toEqual([
      "https://example.com/docs",
      "-o",
      "./packs/example",
      "--format",
      "ndjson",
    ]);
  });

  test("rejects empty url and outputDir", () => {
    expect(() => fetchToPack("", "./packs/example")).toThrow("non-empty url");
    expect(() => fetchToPack("https://example.com", "  ")).toThrow("non-empty outputDir");
  });
});
