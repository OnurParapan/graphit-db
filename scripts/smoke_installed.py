"""Exercise an installed Graphit wheel in a disposable project and MCP session."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from contextlib import closing
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters
from mcp.types import TextContent

import graphit
from graphit.codex import COMPACT_CODEX_TOOLS
from graphit.mcp_launcher import local_mcp_launch
from graphit.scanners.protocol import (
    ColumnMetadata,
    ForeignKeyMetadata,
    IndexMetadata,
    KeyConstraintMetadata,
    MetadataSnapshot,
    SchemaMetadata,
    TableMetadata,
)
from graphit.snapshots import persist_snapshot
from graphit.sources import SourceConfig, add_source

REQUIRED_MCP_TOOLS = {
    "database_overview",
    "get_relevant_context",
    "search_objects",
    "get_table",
    "get_view",
    "get_relationships",
    "get_impact_context",
    "get_transitive_impact",
    "get_column_impact",
    "get_index_context",
    "get_graph_context",
    "find_path",
    "list_snapshots",
    "compare_snapshots",
}


def seed_local_snapshot(project: Path) -> None:
    """Create a tiny saved graph without source credentials or connectivity."""

    source = SourceConfig(
        name="smoke",
        host="localhost",
        port=5432,
        database_name="unused",
        username="reader",
        credential_env="GRAPHIT_SMOKE_UNUSED_PASSWORD",
        schemas=("public",),
    )
    add_source(project, source)
    persist_snapshot(
        project,
        source,
        MetadataSnapshot(
            source_name="smoke",
            schemas=(SchemaMetadata("public"),),
            tables=(
                TableMetadata("public", "customer", False),
                TableMetadata("public", "orders", False),
            ),
            columns=(
                ColumnMetadata("public", "customer", "id", 1, "integer", False),
                ColumnMetadata("public", "orders", "customer_id", 1, "integer", False),
            ),
            keys=(
                KeyConstraintMetadata(
                    "public", "customer", "customer_pkey", "PRIMARY_KEY", ("id",), False
                ),
            ),
            foreign_keys=(
                ForeignKeyMetadata(
                    "public",
                    "orders",
                    "orders_customer_fk",
                    ("customer_id",),
                    "public",
                    "customer",
                    ("id",),
                    True,
                    True,
                    False,
                ),
            ),
            indexes=(
                IndexMetadata(
                    "public",
                    "orders",
                    "orders_customer_lookup",
                    "btree",
                    ("customer_id",),
                    (),
                    False,
                    False,
                    True,
                    True,
                    False,
                ),
            ),
        ),
    )


async def check_mcp(command: str, args: list[str]) -> None:
    process = StdioServerParameters(command=command, args=args)
    with anyio.fail_after(15):
        async with Client(process) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            if names != REQUIRED_MCP_TOOLS:
                raise RuntimeError(
                    "Installed MCP tool list differs from documented tools: "
                    f"missing={REQUIRED_MCP_TOOLS - names}, unexpected={names - REQUIRED_MCP_TOOLS}"
                )
            if not all(
                tool.annotations and tool.annotations.read_only_hint for tool in tools.tools
            ):
                raise RuntimeError("Installed MCP server exposed a non-read-only tool")
            result = await client.call_tool(
                "get_column_impact", {"source": "smoke", "name": "public.customer.id"}
            )
            if result.is_error or result.structured_content is not None or len(result.content) != 1:
                raise RuntimeError("Installed MCP column impact did not return one compact result")
            content = result.content[0]
            if not isinstance(content, TextContent):
                raise RuntimeError("Installed MCP column impact did not return text")
            payload = json.loads(content.text)
            if not isinstance(payload, dict):
                raise RuntimeError("Installed MCP column impact returned invalid JSON")
            references = payload.get("references")
            if (
                payload.get("scope") != "DECLARED_FK_COLUMN_DIRECT"
                or payload.get("reference_count") != 1
                or payload.get("snapshot_version") != 1
                or not isinstance(references, list)
                or len(references) != 1
                or not isinstance(references[0], dict)
                or references[0].get("foreign_key") != "orders_customer_fk"
                or references[0].get("pair_position") != 1
            ):
                raise RuntimeError("Installed MCP column impact returned unexpected FK facts")
            tree_result = await client.call_tool(
                "get_transitive_impact", {"source": "smoke", "name": "public.customer"}
            )
            if (
                tree_result.is_error
                or tree_result.structured_content is not None
                or len(tree_result.content) != 1
                or not isinstance(tree_result.content[0], TextContent)
            ):
                raise RuntimeError("Installed MCP transitive impact did not return one text result")
            tree = json.loads(tree_result.content[0].text)
            if (
                not isinstance(tree, dict)
                or tree.get("scope") != "DECLARED_FK_TRANSITIVE_TABLE"
                or tree.get("dependent_table_count") != 1
                or not isinstance(tree.get("dependents"), list)
                or len(tree["dependents"]) != 1
                or tree["dependents"][0].get("dependent_table") != '"public"."orders"'
                or tree["dependents"][0].get("hops") != 1
            ):
                raise RuntimeError("Installed MCP transitive impact returned unexpected FK facts")
            index_result = await client.call_tool(
                "get_index_context", {"source": "smoke", "name": "public.orders"}
            )
            if (
                index_result.is_error
                or index_result.structured_content is not None
                or len(index_result.content) != 1
                or not isinstance(index_result.content[0], TextContent)
            ):
                raise RuntimeError("Installed MCP index context did not return one compact result")
            index_payload = json.loads(index_result.content[0].text)
            if (
                not isinstance(index_payload, dict)
                or index_payload.get("source_name") != "smoke"
                or index_payload.get("snapshot_version") != 1
                or index_payload.get("index_count") != 1
                or index_payload.get("truncated") is not False
            ):
                raise RuntimeError("Installed MCP index context returned unexpected facts")
            indexes = index_payload.get("indexes")
            if (
                not isinstance(indexes, list)
                or len(indexes) != 1
                or not isinstance(indexes[0], dict)
                or indexes[0].get("name") != "orders_customer_lookup"
                or indexes[0].get("key_columns") != [{"position": 1, "column_name": "customer_id"}]
            ):
                raise RuntimeError("Installed MCP index context lost ordered key facts")


def run_cli(project: Path, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "graphit", *args, "--project", str(project)],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Installed graphit {' '.join(args)} failed: {result.stderr.strip()}")


def run_smoke(project: Path, *, require_installed: bool = True) -> None:
    module_file = graphit.__file__
    if module_file is None:
        raise RuntimeError("Graphit package has no file location")
    if require_installed and not Path(module_file).resolve().is_relative_to(
        Path(sys.prefix).resolve()
    ):
        raise RuntimeError("Graphit was loaded from source, not the isolated installation")

    print("Smoke: initialize project", flush=True)
    run_cli(project, "init")

    config = project / "graphit.toml"
    store = project / ".graphit" / "graphit.db"
    if not config.is_file() or not store.is_file():
        raise RuntimeError("Installed graphit init did not create project configuration and store")
    with closing(sqlite3.connect(f"{store.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    if version < 1 or not {"sources", "snapshots", "objects"} <= tables:
        raise RuntimeError("Installed graphit init created an incomplete SQLite store")

    print("Smoke: seed local snapshot", flush=True)
    seed_local_snapshot(project)

    codex_path = project / ".codex" / "config.toml"
    codex_path.parent.mkdir()
    existing_codex = (
        'model = "existing-model"\n\n'
        '[mcp_servers.other]\ncommand = "other-tool"\nargs = ["--safe"]\n'
    )
    codex_path.write_text(existing_codex, encoding="utf-8")
    existing_codex_bytes = codex_path.read_bytes()
    claude_path = project / ".mcp.json"
    existing_claude_other: dict[str, object] = {
        "type": "stdio",
        "command": "other-tool",
        "args": [],
    }
    existing_claude = {
        "projectSetting": "keep",
        "mcpServers": {"other": existing_claude_other},
    }
    claude_path.write_text(json.dumps(existing_claude), encoding="utf-8")

    print("Smoke: configure Codex and Claude", flush=True)
    run_cli(project, "mcp", "setup-codex")
    run_cli(project, "mcp", "setup-claude")
    run_cli(project, "mcp", "setup-codex", "--refresh", "--compact-tools")

    codex_bytes = codex_path.read_bytes()
    if not codex_bytes.startswith(existing_codex_bytes):
        raise RuntimeError("Codex setup changed unrelated project configuration bytes")
    codex = tomllib.loads(codex_bytes.decode("utf-8"))
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    if codex.get("model") != "existing-model" or codex["mcp_servers"]["other"] != {
        "command": "other-tool",
        "args": ["--safe"],
    }:
        raise RuntimeError("Codex setup changed unrelated project settings")
    claude_other = claude["mcpServers"]["other"]
    if claude.get("projectSetting") != "keep" or claude_other != existing_claude_other:
        raise RuntimeError("Claude setup changed unrelated project settings")

    expected_command, expected_args = local_mcp_launch(project)
    codex_entry = codex["mcp_servers"]["graphit"]
    claude_entry = claude["mcpServers"]["graphit"]
    if codex_entry.get("enabled_tools") != list(COMPACT_CODEX_TOOLS):
        raise RuntimeError("Codex compact setup did not write the exact core tool allowlist")
    for name, entry in (("Codex", codex_entry), ("Claude", claude_entry)):
        if entry.get("command") != expected_command or entry.get("args") != expected_args:
            raise RuntimeError(f"{name} setup did not use the installed Graphit launcher")
        if name == "Claude" and entry.get("type") != "stdio":
            raise RuntimeError("Claude setup did not declare the stdio transport")
        print(f"Smoke: exercise {name} MCP entry", flush=True)
        anyio.run(check_mcp, entry["command"], entry["args"])
        print(f"Smoke: {name} MCP entry passed", flush=True)


if __name__ == "__main__":
    temp_base = Path.cwd() / ".pytest-tmp"
    temp_base.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="graphit-package-smoke-", dir=temp_base) as path:
        run_smoke(Path(path))
    print("Installed Graphit init and Codex/Claude MCP tool smoke passed")
