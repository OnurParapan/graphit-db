"""Project initialization and discovery tests."""

import sqlite3
import tomllib
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from graphit.project import ProjectError, find_project_root, initialize_project


def test_initialize_git_project_with_local_store(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    result = initialize_project(tmp_path)

    assert {path.name for path in result.created} == {
        "graphit.toml",
        ".graphit",
        "graphit.db",
        ".gitignore",
    }
    assert result.updated == ()
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == ".graphit/\n"
    assert {path.name for path in (tmp_path / ".graphit").iterdir()} == {"graphit.db"}
    with sqlite3.connect(tmp_path / ".graphit" / "graphit.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    config = tomllib.loads((tmp_path / "graphit.toml").read_text(encoding="utf-8"))
    assert config["sampling"]["enabled"] is False
    assert config["scan"]["infer_relationships"] is False
    assert "password" not in config


def test_non_git_project_does_not_create_gitignore(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("graphit.project._is_git_project", lambda _: False)
    initialize_project(tmp_path)

    assert (tmp_path / "graphit.toml").exists()
    assert not (tmp_path / ".gitignore").exists()


def test_find_root_prefers_nearest_existing_graphit_config(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "subproject" / "src"
    nested.mkdir(parents=True)
    (nested.parent / "graphit.toml").write_text("[scan]\n", encoding="utf-8")

    assert find_project_root(nested) == nested.parent


def test_find_root_uses_git_root_for_nested_directory(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "src" / "package"
    nested.mkdir(parents=True)

    assert find_project_root(nested) == tmp_path


def test_find_root_does_not_escape_nearest_git_boundary(tmp_path: Path) -> None:
    (tmp_path / "graphit.toml").write_text("[scan]\n", encoding="utf-8")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src"
    nested.mkdir()

    assert find_project_root(nested) == repo


def test_existing_config_is_untouched_without_force(tmp_path: Path) -> None:
    config = tmp_path / "graphit.toml"
    config.write_text("[custom]\nvalue = 3\n", encoding="utf-8")

    with pytest.raises(ProjectError, match="Use --force"):
        initialize_project(tmp_path)

    assert config.read_text(encoding="utf-8") == "[custom]\nvalue = 3\n"
    assert not (tmp_path / ".graphit").exists()


def test_force_replaces_config_but_preserves_existing_state(tmp_path: Path) -> None:
    state = tmp_path / ".graphit"
    state.mkdir()
    existing_data = state / "keep.txt"
    existing_data.write_text("keep me", encoding="utf-8")
    config = tmp_path / "graphit.toml"
    config.write_text("obsolete", encoding="utf-8")

    result = initialize_project(tmp_path, force=True)

    assert result.updated == (config,)
    assert existing_data.read_text(encoding="utf-8") == "keep me"
    assert tomllib.loads(config.read_text(encoding="utf-8"))["scan"]["schemas"] == ["public"]
    assert (state / "graphit.db").is_file()


def test_force_preserves_existing_store_rows(tmp_path: Path) -> None:
    initialize_project(tmp_path)
    store = tmp_path / ".graphit" / "graphit.db"
    with sqlite3.connect(store) as connection:
        connection.execute(
            "INSERT INTO review_decisions "
            "(source_logical_key, target_logical_key, relationship_kind, decision) "
            "VALUES ('a', 'b', 'REFERENCES', 'APPROVED')"
        )

    result = initialize_project(tmp_path, force=True)

    assert store not in result.created
    assert store not in result.updated
    with sqlite3.connect(store) as connection:
        assert connection.execute("SELECT count(*) FROM review_decisions").fetchone()[0] == 1


def test_invalid_existing_store_does_not_replace_config(tmp_path: Path) -> None:
    state = tmp_path / ".graphit"
    state.mkdir()
    (state / "graphit.db").write_bytes(b"not a SQLite database")
    config = tmp_path / "graphit.toml"
    config.write_text("keep me", encoding="utf-8")

    with pytest.raises(ProjectError, match="Cannot initialize local store"):
        initialize_project(tmp_path, force=True)

    assert config.read_text(encoding="utf-8") == "keep me"


def test_gitignore_append_is_idempotent_across_force(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    ignore = tmp_path / ".gitignore"
    ignore.write_text("build/", encoding="utf-8")

    initialize_project(tmp_path)
    second = initialize_project(tmp_path, force=True)

    assert ignore.read_text(encoding="utf-8") == "build/\n.graphit/\n"
    assert ignore not in second.updated


def test_gitignore_keeps_existing_crlf_line_endings(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    ignore = tmp_path / ".gitignore"
    ignore.write_bytes(b"build/\r\ndist/\r\n")

    initialize_project(tmp_path)

    assert ignore.read_bytes() == b"build/\r\ndist/\r\n.graphit/\r\n"


def test_explicit_nested_project_under_git_gets_local_ignore(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "subpackage"
    nested.mkdir()

    initialize_project(nested)

    assert (nested / ".gitignore").read_text(encoding="utf-8") == ".graphit/\n"


def test_symlinked_config_is_rejected_before_any_write(tmp_path: Path) -> None:
    original = tmp_path / "outside.toml"
    original.write_text("original", encoding="utf-8")
    try:
        (tmp_path / "graphit.toml").symlink_to(original)
    except OSError as error:
        pytest.skip(f"Symlink creation is not permitted on this system: {error}")

    with pytest.raises(ProjectError, match="symlinked"):
        initialize_project(tmp_path, force=True)

    assert original.read_text(encoding="utf-8") == "original"
    assert not (tmp_path / ".graphit").exists()
