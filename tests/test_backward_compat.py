"""Tests for backward-compatible entry point settings migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from cocoindex_code.server import _convert_embedding_model
from cocoindex_code.settings import (
    EmbeddingSettings,
    LanguageOverride,
    UserSettings,
    default_project_settings,
    default_user_settings,
    find_legacy_project_root,
    load_project_settings,
    load_user_settings,
    save_project_settings,
    save_user_settings,
)


def test_legacy_entry_creates_settings_from_env_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings migration from env vars should produce correct YAML values."""
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_dir",
        lambda: tmp_path / "user",
    )
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_path",
        lambda: tmp_path / "user" / "global_settings.yml",
    )

    # Simulate env vars
    us = default_user_settings()
    provider, model = _convert_embedding_model("sbert/sentence-transformers/all-MiniLM-L6-v2")
    us.embedding = EmbeddingSettings(provider=provider, model=model, device="cpu")
    save_user_settings(us)

    loaded = load_user_settings()
    assert loaded.embedding.provider == "sentence-transformers"
    assert "all-MiniLM-L6-v2" in loaded.embedding.model
    assert loaded.embedding.device == "cpu"


def test_legacy_entry_respects_existing_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing settings files should not be overwritten."""
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_dir",
        lambda: tmp_path / "user",
    )
    monkeypatch.setattr(
        "cocoindex_code.settings.user_settings_path",
        lambda: tmp_path / "user" / "global_settings.yml",
    )

    custom = UserSettings(
        embedding=EmbeddingSettings(provider="litellm", model="custom/model"),
    )
    save_user_settings(custom)

    # Loading should return existing settings
    loaded = load_user_settings()
    assert loaded.embedding.provider == "litellm"
    assert loaded.embedding.model == "custom/model"


def test_legacy_embedding_model_conversion() -> None:
    """Old sbert/ prefix and litellm-style model names should be converted correctly."""
    provider, model = _convert_embedding_model("sbert/sentence-transformers/all-MiniLM-L6-v2")
    assert provider == "sentence-transformers"
    assert model == "sentence-transformers/all-MiniLM-L6-v2"

    provider, model = _convert_embedding_model("gemini/text-embedding-004")
    assert provider == "litellm"
    assert model == "gemini/text-embedding-004"


def test_legacy_extra_extensions_conversion(tmp_path: Path) -> None:
    """COCOINDEX_CODE_EXTRA_EXTENSIONS should produce language_overrides and include_patterns."""
    ps = default_project_settings()

    # Simulate: "inc:php,yaml,toml"
    raw = "inc:php,yaml,toml"
    for token in raw.split(","):
        token = token.strip()
        if ":" in token:
            ext, lang = token.split(":", 1)
            ps.include_patterns.append(f"**/*.{ext.strip()}")
            ps.language_overrides.append(LanguageOverride(ext=ext.strip(), lang=lang.strip()))
        else:
            ps.include_patterns.append(f"**/*.{token}")

    save_project_settings(tmp_path, ps)
    loaded = load_project_settings(tmp_path)

    assert any(lo.ext == "inc" and lo.lang == "php" for lo in loaded.language_overrides)
    assert "**/*.inc" in loaded.include_patterns
    assert "**/*.yaml" in loaded.include_patterns
    assert "**/*.toml" in loaded.include_patterns


def test_legacy_root_discovery_requires_cocoindex_db(tmp_path: Path) -> None:
    """A .cocoindex_code dir without cocoindex.db should not be matched."""
    (tmp_path / ".cocoindex_code").mkdir()
    assert find_legacy_project_root(tmp_path) is None


def test_legacy_root_discovery_with_cocoindex_db(tmp_path: Path) -> None:
    """A .cocoindex_code dir with cocoindex.db should be matched, including from a subdirectory."""
    idx_dir = tmp_path / ".cocoindex_code"
    idx_dir.mkdir()
    (idx_dir / "cocoindex.db").touch()

    # Exact directory
    assert find_legacy_project_root(tmp_path) == tmp_path

    # From a subdirectory — should walk up and find the root
    sub = tmp_path / "src" / "pkg"
    sub.mkdir(parents=True)
    assert find_legacy_project_root(sub) == tmp_path


def test_legacy_excluded_patterns_conversion(tmp_path: Path) -> None:
    """COCOINDEX_CODE_EXCLUDED_PATTERNS should be appended to default exclude_patterns."""

    ps = default_project_settings()
    extra = ["**/migration.sql"]
    ps.exclude_patterns.extend(extra)

    save_project_settings(tmp_path, ps)
    loaded = load_project_settings(tmp_path)

    # Should have defaults + extra
    assert "**/migration.sql" in loaded.exclude_patterns
    assert "**/.*" in loaded.exclude_patterns  # default


def test_legacy_entry_does_not_auto_init_user_settings_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Started from $HOME, the legacy server exits with a message instead of making
    $HOME a project (or dying with a traceback)."""
    from cocoindex_code.server import main

    home = tmp_path / "home"
    (home / ".cocoindex_code").mkdir(parents=True)
    (home / ".cocoindex_code" / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COCOINDEX_CODE_DIR", raising=False)
    monkeypatch.delenv("COCOINDEX_CODE_ROOT_PATH", raising=False)
    monkeypatch.chdir(home)
    monkeypatch.setattr("sys.argv", ["cocoindex-code", "index"])

    def _no_daemon(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the legacy entry point reached the daemon")

    monkeypatch.setattr("cocoindex_code.client.index", _no_daemon)

    with pytest.raises(SystemExit) as exc:
        main()

    assert "user settings directory" in str(exc.value.code)
    assert not (home / ".cocoindex_code" / "settings.yml").exists()


def test_legacy_entry_honors_explicit_root_path_at_user_settings_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """COCOINDEX_CODE_ROOT_PATH=$HOME is a deliberate choice, like `ccc init`: the
    legacy server initializes $HOME instead of refusing it."""
    from cocoindex_code.protocol import IndexResponse
    from cocoindex_code.server import main

    home = tmp_path / "home"
    (home / ".cocoindex_code").mkdir(parents=True)
    (home / ".cocoindex_code" / "global_settings.yml").write_text(
        "embedding:\n  model: test\n  provider: litellm\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("COCOINDEX_CODE_DIR", raising=False)
    monkeypatch.setenv("COCOINDEX_CODE_ROOT_PATH", str(home))
    monkeypatch.chdir(home)
    monkeypatch.setattr("sys.argv", ["cocoindex-code", "index"])
    indexed: list[str] = []

    def _index(root: str, **_kwargs: object) -> IndexResponse:
        indexed.append(root)
        return IndexResponse(success=False, message="stub")

    monkeypatch.setattr("cocoindex_code.client.index", _index)

    main()

    assert (home / ".cocoindex_code" / "settings.yml").is_file()
    assert indexed == [str(home.resolve())]
