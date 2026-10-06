"""OMP marketplace/plugin manifests stay valid, launch `ccc mcp`, and declare index hooks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_omp_marketplace_catalog_points_at_repo_root() -> None:
    catalog = json.loads((REPO_ROOT / ".omp-plugin" / "marketplace.json").read_text())
    assert catalog["name"] == "cocoindex-code"
    assert catalog["owner"]["name"] == "CocoIndex"
    assert catalog["plugins"][0]["name"] == "cocoindex-code"
    assert catalog["plugins"][0]["source"] == "./"


def test_omp_plugin_manifest_replaces_mcp_with_ccc_mcp() -> None:
    manifest = json.loads((REPO_ROOT / ".omp-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "cocoindex-code"
    assert manifest["mcpServers"] == "./.mcp.json"
    mcp = json.loads((REPO_ROOT / ".mcp.json").read_text())
    assert mcp["mcpServers"]["cocoindex-code"] == {"command": "ccc", "args": ["mcp"]}


def test_omp_package_declares_ccc_index_extension() -> None:
    package = json.loads((REPO_ROOT / "package.json").read_text())
    entries = package["omp"]["extensions"]
    assert entries == ["./extensions/ccc-index.ts"]
    extension = REPO_ROOT / "extensions" / "ccc-index.ts"
    assert extension.is_file()
    source = extension.read_text()
    assert 'pi.on("session_start"' in source
    assert 'pi.on("tool_result"' in source


def test_claude_hooks_cover_session_start_and_post_edit() -> None:
    hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    assert "SessionStart" in hooks
    assert "PostToolUse" in hooks
    matcher = hooks["PostToolUse"][0]["matcher"]
    assert "Edit" in matcher
    assert "Write" in matcher
    # One command for both events: the behavior tests below run each, but only
    # equality keeps a fix to one from silently missing the other.
    commands = _hook_commands()
    assert commands["PostToolUse"] == commands["SessionStart"]


def _hook_commands() -> dict[str, str]:
    hooks = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    return {
        event: h["command"] for event, entries in hooks.items() for e in entries for h in e["hooks"]
    }


def _fake_ccc(tmp_path: Path) -> tuple[Path, Path]:
    """A `ccc` on PATH that logs the directory it ran in; returns (bin dir, log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "ccc.log"
    fake_ccc = bin_dir / "ccc"
    fake_ccc.write_text(f'#!/bin/sh\necho "$PWD $*" >> "{log}"\n')
    fake_ccc.chmod(0o755)
    return bin_dir, log


def _home_with_stale_project(tmp_path: Path) -> tuple[Path, Path]:
    """A HOME that an older ccc auto-initialized, plus a real project below it."""
    home = tmp_path / "home"
    (home / ".cocoindex_code").mkdir(parents=True)
    (home / ".cocoindex_code" / "global_settings.yml").write_text("embedding: {model: x}\n")
    (home / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")
    project = home / "project"
    (project / ".cocoindex_code").mkdir(parents=True)
    (project / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")
    return home, project


def _test_env(bin_dir: Path, home: Path) -> dict[str, str]:
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "HOME": str(home)}
    env.pop("COCOINDEX_CODE_DIR", None)
    return env


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="hooks are bash commands"
)
@pytest.mark.parametrize("command", list(_hook_commands().values()), ids=list(_hook_commands()))
def test_hook_indexes_projects_but_not_user_settings_root(command: str, tmp_path: Path) -> None:
    """The hook indexes a dir with ``settings.yml`` but never the dir holding the user
    settings dir (``$HOME``, or the parent of a ``.cocoindex_code`` COCOINDEX_CODE_DIR),
    even when an older ccc left a ``settings.yml`` there.
    """
    bin_dir, log = _fake_ccc(tmp_path)
    home, project = _home_with_stale_project(tmp_path)
    workspace = tmp_path / "workspace"
    (workspace / ".cocoindex_code").mkdir(parents=True)
    (workspace / ".cocoindex_code" / "settings.yml").write_text("include_patterns: []\n")

    def run(root: Path, **extra_env: str) -> None:
        env = {**_test_env(bin_dir, home), "CLAUDE_PROJECT_DIR": str(root), **extra_env}
        subprocess.run(["bash", "-c", command], env=env, check=True, cwd=tmp_path)

    run(home)
    run(workspace, COCOINDEX_CODE_DIR=str(workspace / ".cocoindex_code"))
    assert not log.exists()

    run(project)
    assert log.read_text().strip() == f"{project} index"


def _node_strips_types() -> bool:
    node = shutil.which("node")
    if node is None:
        return False
    probe = subprocess.run(
        [node, "-p", "Boolean(process.features.typescript)"], capture_output=True, text=True
    )
    return probe.stdout.strip() == "true"


_EXTENSION_DRIVER = """
import { pathToFileURL } from "node:url";
const [extension, ...cwds] = process.argv.slice(1);
const { default: cccIndex } = await import(pathToFileURL(extension).href);
const handlers = {};
cccIndex({ on: (event, handler) => { handlers[event] = handler; } });
for (const cwd of cwds) await handlers.session_start({}, { cwd });
"""


@pytest.mark.skipif(sys.platform == "win32", reason="the fake ccc is a shell script")
@pytest.mark.skipif(not _node_strips_types(), reason="needs node with TypeScript type stripping")
def test_omp_extension_indexes_projects_but_not_user_settings_root(tmp_path: Path) -> None:
    """Same contract as the hooks, through the real extension module."""
    bin_dir, log = _fake_ccc(tmp_path)
    home, project = _home_with_stale_project(tmp_path)
    (project / "src").mkdir()

    subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            _EXTENSION_DRIVER,
            str(REPO_ROOT / "extensions" / "ccc-index.ts"),
            str(home),
            str(project / "src"),
        ],
        env=_test_env(bin_dir, home),
        check=True,
        cwd=tmp_path,
    )

    assert log.read_text().strip() == f"{project} index"
