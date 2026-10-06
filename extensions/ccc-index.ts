/**
 * Oh My Pi extension — same contract as Claude/Grok hooks/hooks.json:
 *   session_start              → incremental `ccc index` if the project is initialized
 *   tool_result after edits    → same (Edit/Write/MultiEdit/search_replace + OMP names)
 *
 * Fail-open: missing `ccc`, an uninitialized project (no `.cocoindex_code/settings.yml`),
 * or a failed index never throws into the session.
 */

import { spawn } from "node:child_process";
import { realpathSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";

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

// ccc's user settings dir (global_settings.yml, daemon files). Auto-indexing never
// runs in the directory that contains it: a project there covers every directory
// below it, and is usually left over from an older ccc that auto-initialized $HOME.
function userSettingsDir(): string {
  return resolvedPath(
    process.env.COCOINDEX_CODE_DIR || join(homedir(), ".cocoindex_code"),
  );
}

// Like Python's Path.resolve(): follows symlinks, tolerates missing paths.
function resolvedPath(path: string): string {
  try {
    return realpathSync(path);
  } catch {
    return resolve(path);
  }
}

function isFile(path: string): boolean {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

// Mirrors ccc's find_project_root (`.cocoindex_code/settings.yml` marks a project),
// except that it never returns the directory holding the user settings dir.
function findInitializedRoot(cwd: string): string | null {
  const userDir = userSettingsDir();
  let dir = cwd;
  for (let i = 0; i < 32; i++) {
    const settingsDir = join(dir, ".cocoindex_code");
    if (
      isFile(join(settingsDir, "settings.yml")) &&
      resolvedPath(settingsDir) !== userDir
    ) {
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
