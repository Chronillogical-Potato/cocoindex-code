/**
 * Oh My Pi extension — same contract as Claude/Grok hooks/hooks.json:
 *   session_start              → incremental `ccc index` if the project is initialized
 *   tool_result after edits    → same (Edit/Write/MultiEdit/search_replace + OMP names)
 *
 * Fail-open: missing `ccc`, missing `.cocoindex_code/`, or a failed index never
 * throws into the session.
 */

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";

const INDEX_TIMEOUT_MS = 60_000;

const EDIT_TOOLS: Record<string, true> = {
  edit: true,
  write: true,
  ast_edit: true,
  multiedit: true,
  search_replace: true,
};

type IndexCtx = { cwd: string };

type ToolResultEvent = {
  toolName?: string;
  isError?: boolean;
};

type ExtensionApi = {
  setLabel?: (label: string) => void;
  on: (
    event: string,
    handler: (event: ToolResultEvent, ctx: IndexCtx) => unknown,
  ) => void;
};

function findInitializedRoot(cwd: string): string | null {
  let dir = cwd;
  for (let i = 0; i < 32; i++) {
    if (existsSync(join(dir, ".cocoindex_code"))) {
      return dir;
    }
    const parent = dirname(dir);
    if (parent === dir) {
      return null;
    }
    dir = parent;
  }
  return null;
}

function runIndex(root: string): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  const child = spawn("ccc", ["index"], {
    cwd: root,
    stdio: "ignore",
  });
  const timer = setTimeout(() => {
    child.kill("SIGTERM");
    resolve();
  }, INDEX_TIMEOUT_MS);
  const done = () => {
    clearTimeout(timer);
    resolve();
  };
  child.on("error", done);
  child.on("close", done);
  return promise;
}

export default function cccIndex(pi: ExtensionApi): void {
  pi.setLabel?.("ccc-index");

  let running = false;
  let queued = false;

  const reindex = async (ctx: IndexCtx): Promise<void> => {
    if (running) {
      queued = true;
      return;
    }
    running = true;
    try {
      do {
        queued = false;
        const root = findInitializedRoot(ctx.cwd);
        if (root) {
          await runIndex(root);
        }
      } while (queued);
    } finally {
      running = false;
    }
  };

  pi.on("session_start", async (_event, ctx) => {
    await reindex(ctx);
  });

  pi.on("tool_result", async (event, ctx) => {
    if (event.isError) {
      return;
    }
    const name = String(event.toolName ?? "").toLowerCase();
    if (!EDIT_TOOLS[name]) {
      return;
    }
    await reindex(ctx);
  });
}
