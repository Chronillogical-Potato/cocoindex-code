"""OMP marketplace/plugin manifests stay valid, launch `ccc mcp`, and declare index hooks."""

from __future__ import annotations

import json
from pathlib import Path

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
