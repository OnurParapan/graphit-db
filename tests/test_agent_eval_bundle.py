"""Offline agent evaluation packets are complete, stable, and non-destructive."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest
from pytest import MonkeyPatch

import scripts.prepare_agent_eval as evaluation
from graphit.queries import TableContext, show_table
from tests.test_inference_benchmark import _save_fixture


def test_bundle_has_equal_answer_facts_without_source_or_model(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    def no_connection(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Offline evaluation must not connect to PostgreSQL")

    monkeypatch.setattr("psycopg.connect", no_connection)
    output = tmp_path / "prepared"
    digest = evaluation.prepare_eval_bundle(output)
    project = output / "graphit-project"
    baseline = output / "baseline-arm" / "baseline.json"
    packet_bytes = baseline.read_bytes()
    packet = json.loads(packet_bytes)
    assert (project / ".graphit" / "graphit.db").is_file()
    assert digest == hashlib.sha256(packet_bytes).hexdigest()
    assert packet_bytes == evaluation.build_baseline_packet(project)
    assert packet["source_name"] == "erp" and packet["snapshot_version"] == 1
    assert (packet["table_count"], packet["column_count"], packet["foreign_key_count"]) == (
        14,
        20,
        1,
    )
    assert not list(output.rglob("*answer*"))
    payment = next(
        table for table in packet["tables"] if table["qualified_name"] == '"sales"."payment"'
    )
    foreign_key = payment["foreign_keys"][0]
    assert foreign_key == {
        "name": "payment_customer_fk",
        "source_table": '"sales"."payment"',
        "target_table": '"crm"."customer"',
        "column_pairs": [["customer_id", "id"]],
        "target_in_scope": True,
        "validated": True,
        "inherited": False,
        "origin": "DATABASE",
        "status": "CONFIRMED",
    }
    assert all(
        key["target_table"] != '"legacy"."customer"'
        for table in packet["tables"]
        for key in table["foreign_keys"]
    )


def test_baseline_packet_is_byte_stable_and_cli_entry_works(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_digest = evaluation.prepare_eval_bundle(first)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.prepare_agent_eval", str(second)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    first_bytes = (first / "baseline-arm" / "baseline.json").read_bytes()
    second_bytes = (second / "baseline-arm" / "baseline.json").read_bytes()
    assert first_bytes == second_bytes
    assert first_digest in result.stdout


def test_scaled_bundle_is_complete_stable_and_cli_selectable(tmp_path: Path) -> None:
    first = tmp_path / "scaled-first"
    second = tmp_path / "scaled-second"
    digest = evaluation.prepare_eval_bundle(first, scaled=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.prepare_agent_eval",
            str(second),
            "--scaled",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    first_bytes = (first / "baseline-arm" / "baseline.json").read_bytes()
    second_bytes = (second / "baseline-arm" / "baseline.json").read_bytes()
    packet = json.loads(first_bytes)
    assert first_bytes == second_bytes
    assert digest == hashlib.sha256(first_bytes).hexdigest()
    assert digest in result.stdout
    assert (packet["table_count"], packet["column_count"], packet["foreign_key_count"]) == (
        114,
        420,
        1,
    )
    archive_tables = [
        table for table in packet["tables"] if table["qualified_name"].startswith('"archive".')
    ]
    assert len(archive_tables) == 100
    assert all(len(table["columns"]) == 4 for table in archive_tables)
    assert sum(len(table["foreign_keys"]) for table in packet["tables"]) == 1


def test_bundle_refuses_existing_output_without_touching_it(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("unchanged", encoding="utf-8")
    with pytest.raises(FileExistsError):
        evaluation.prepare_eval_bundle(output)
    assert marker.read_text(encoding="utf-8") == "unchanged"
    assert sorted(item.name for item in output.iterdir()) == ["keep.txt"]


def test_baseline_rejects_missing_fk_and_truncation(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _save_fixture(project)
    original = show_table

    def truncated(root: Path, source_name: str, reference: str, limit: int = 100) -> TableContext:
        return replace(original(root, source_name, reference, limit), columns_truncated=True)

    monkeypatch.setattr(evaluation, "show_table", truncated)
    with pytest.raises(ValueError, match="incomplete"):
        evaluation.build_baseline_packet(project)
    monkeypatch.setattr(evaluation, "show_table", original)
    with closing(sqlite3.connect(project / ".graphit" / "graphit.db")) as connection:
        with connection:
            connection.execute("DELETE FROM edges WHERE edge_type = 'REFERENCES'")
    with pytest.raises(ValueError, match="incomplete|differ"):
        evaluation.build_baseline_packet(project)
