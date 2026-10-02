"""Graphit's command-line entry point."""

import json
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Literal

import typer

from graphit import __version__
from graphit.claude import ClaudeSetupError, setup_claude
from graphit.codex import CodexSetupError, setup_codex
from graphit.discovery import discover_postgresql
from graphit.graph_dot import render_dot
from graphit.graph_export import table_graph
from graphit.graph_html import render_html
from graphit.inference import MAX_CANDIDATE_LIMIT, preview_candidates
from graphit.mcp_server import create_server
from graphit.project import ProjectError, find_project_root, initialize_project
from graphit.queries import (
    DEFAULT_IMPACT_LIMIT,
    DEFAULT_INDEX_LIMIT,
    DEFAULT_PATH_HOPS,
    MAX_IMPACT_LIMIT,
    MAX_INDEX_LIMIT,
    MAX_INDEX_OFFSET,
    MAX_PATH_HOPS,
    MAX_RELATIONSHIP_LIMIT,
    MAX_SEARCH_LIMIT,
    MAX_SHOW_LIMIT,
    QueryError,
    column_impact,
    find_table_path,
    list_table_indexes,
    search_objects,
    show_table,
    show_view,
    table_impact,
    table_relationships,
    transitive_table_impact,
)
from graphit.review import (
    DEFAULT_PROPOSAL_LIMIT,
    MAX_PROPOSAL_LIMIT,
    approve_candidate,
    approve_manual_proposal,
    list_proposals,
    propose_relationship,
    reject_candidate,
    restore_candidate,
    revoke_approval,
    revoke_manual_proposal,
)
from graphit.scanners.postgresql import ConnectionTestError, MetadataScanError, verify_connection
from graphit.snapshot_diff import (
    DEFAULT_DIFF_LIMIT,
    MAX_DIFF_LIMIT,
    MAX_DIFF_OFFSET,
    compare_snapshots,
)
from graphit.snapshot_history import DEFAULT_HISTORY_LIMIT, MAX_HISTORY_LIMIT, list_snapshots
from graphit.snapshots import SnapshotError, scan_source
from graphit.sources import (
    ProjectNotInitialized,
    SourceConfig,
    SourceError,
    add_source,
    list_sources,
    require_store_path,
    show_source,
)

app = typer.Typer(
    name="graphit",
    help="Turn database structure into compact context for AI coding agents.",
    no_args_is_help=True,
)
source_app = typer.Typer(help="Manage and verify PostgreSQL sources.")
app.add_typer(source_app, name="source")
mcp_app = typer.Typer(help="Serve local database knowledge to AI agents.")
app.add_typer(mcp_app, name="mcp")
review_app = typer.Typer(help="Review and propose logical relationships locally.")
app.add_typer(review_app, name="review")


@app.callback()
def main() -> None:
    """Manage Graphit's local database knowledge and agent context."""


@app.command()
def version() -> None:
    """Print the installed Graphit version."""

    typer.echo(f"Graphit {__version__}")


@app.command()
def init(
    project: Annotated[Path | None, typer.Option(help="Project directory override.")] = None,
    force: Annotated[bool, typer.Option(help="Replace existing graphit.toml.")] = False,
    connect: Annotated[
        bool, typer.Option("--connect/--no-connect", help="Verify discovered databases.")
    ] = True,
    yes: Annotated[
        bool, typer.Option("--yes", help="Accept every discovered database without prompting.")
    ] = False,
    scan_metadata: Annotated[
        bool, typer.Option("--scan/--no-scan", help="Scan verified databases immediately.")
    ] = True,
) -> None:
    """Initialize Graphit, verify discovered databases, and scan their metadata."""

    try:
        root = project if project is not None else find_project_root(Path.cwd())
        result = initialize_project(root, force=force)
    except ProjectError as error:
        typer.echo(f"PROJECT_INIT_FAILED: {error}", err=True)
        raise typer.Exit(code=2) from error

    typer.echo(f"Initialized Graphit in {result.root}")
    for path in result.created:
        typer.echo(f"created: {path.relative_to(result.root)}")
    for path in result.updated:
        typer.echo(f"updated: {path.relative_to(result.root)}")

    candidates = discover_postgresql(result.root)
    if not candidates:
        typer.echo(
            "No PostgreSQL connection URL discovered in the environment or project .env files."
        )
        return
    for candidate in candidates:
        password_status = "present (hidden)" if candidate.has_password else "not present"
        typer.echo(f"Discovered PostgreSQL from {candidate.origin} -> {candidate.variable_name}")
        typer.echo(f"  Host: {candidate.host}:{candidate.port}")
        typer.echo(f"  Database: {candidate.database_name}")
        typer.echo(f"  User: {candidate.username}")
        typer.echo(f"  Password: {password_status}")
        typer.echo(f"  SSL mode: {candidate.ssl_mode}")
    if not connect:
        typer.echo("Database connection skipped by --no-connect.")
        return

    existing = list_sources(result.root)
    used_names = {source.name for source in existing}
    for candidate in candidates:
        credential_kind = "url_env" if candidate.origin == "environment" else "url_dotenv"
        credential_file = None if candidate.origin == "environment" else candidate.origin
        if any(
            source.host.casefold() == candidate.host.casefold()
            and source.port == candidate.port
            and source.database_name == candidate.database_name
            and source.username == candidate.username
            and source.credential_env == candidate.variable_name
            and source.credential_kind == credential_kind
            and source.credential_file == credential_file
            for source in existing
        ):
            typer.echo(f"Source for {candidate.variable_name} is already configured.")
            continue
        if not candidate.has_password:
            typer.echo(f"Skipped {candidate.variable_name}: the PostgreSQL URL has no password.")
            continue
        if not yes and not typer.confirm(
            f"Connect read-only to {candidate.host}:{candidate.port}/{candidate.database_name}?",
            default=True,
        ):
            typer.echo(f"Skipped {candidate.variable_name} by user choice.")
            continue
        source_name = _discovered_source_name(candidate.database_name, used_names)
        source = SourceConfig(
            name=source_name,
            host=candidate.host,
            port=candidate.port,
            database_name=candidate.database_name,
            username=candidate.username,
            credential_env=candidate.variable_name,
            schemas=("public",),
            ssl_mode=candidate.ssl_mode,
            credential_kind=credential_kind,
            credential_file=credential_file,
        )
        try:
            verified = verify_connection(source, project_root=result.root)
            source = replace(source, schemas=verified.schemas)
            add_source(result.root, source)
        except ConnectionTestError as error:
            typer.echo(f"{error.code}: {error}", err=True)
            raise typer.Exit(code=4) from None
        except SourceError as error:
            _source_failure(error)
        used_names.add(source_name)
        typer.echo(
            f"Added source '{source_name}' after read-only verification: "
            f"PostgreSQL {verified.server_version}, {verified.database} as {verified.username}."
        )
        typer.echo(f"  Schemas: {', '.join(verified.schemas)}")
        if not scan_metadata:
            typer.echo(f"Metadata scan skipped for '{source_name}' by --no-scan.")
            continue
        try:
            snapshot = scan_source(result.root, source_name)
        except MetadataScanError as error:
            typer.echo(f"SCAN_FAILED: {error.code}: {error}", err=True)
            raise typer.Exit(code=5) from None
        except SnapshotError as error:
            typer.echo(f"SCAN_FAILED: {error.code}: {error}", err=True)
            raise typer.Exit(code=6) from None
        typer.echo(
            f"Snapshot {snapshot.version} saved for {source_name}: "
            f"{snapshot.object_count} objects, {snapshot.edge_count} edges."
        )


def _discovered_source_name(database_name: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", database_name).strip("-_").lower()
    if not base or not base[0].isalpha():
        base = f"db-{base}" if base else "database"
    base = base[:64]
    candidate = base
    suffix = 2
    while candidate in used:
        ending = f"-{suffix}"
        candidate = f"{base[: 64 - len(ending)]}{ending}"
        suffix += 1
    return candidate


def _source_root(project: Path | None) -> Path:
    if project is not None:
        return project
    try:
        return find_project_root(Path.cwd())
    except ProjectError as error:
        raise ProjectNotInitialized(str(error)) from error


def _source_failure(error: SourceError) -> None:
    code = 3 if isinstance(error, ProjectNotInitialized) else 2
    label = "PROJECT_NOT_INITIALIZED" if code == 3 else "SOURCE_CONFIG_FAILED"
    typer.echo(f"{label}: {error}", err=True)
    raise typer.Exit(code=code) from error


@mcp_app.command("serve")
def mcp_serve(
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Serve read-only local snapshot tools over MCP stdio."""

    try:
        root = _source_root(project)
        require_store_path(root)
    except SourceError as error:
        _source_failure(error)
    create_server(root).run(transport="stdio")


@mcp_app.command("setup-codex")
def mcp_setup_codex(
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
    refresh: Annotated[
        bool, typer.Option(help="Replace only an old generated Graphit launcher.")
    ] = False,
    compact_tools: Annotated[
        bool,
        typer.Option(
            "--compact-tools",
            help="Expose only core schema-navigation tools to reduce Codex tool context.",
        ),
    ] = False,
) -> None:
    """Opt in to project-local Codex MCP configuration."""

    try:
        root = _source_root(project)
        require_store_path(root)
        result = setup_codex(root, refresh=refresh, compact_tools=compact_tools)
    except SourceError as error:
        _source_failure(error)
    except CodexSetupError as error:
        typer.echo(f"CODEX_SETUP_FAILED: {error}", err=True)
        raise typer.Exit(code=2) from error
    state = "updated" if result.changed else "already configured"
    typer.echo(f"Codex MCP {state}: {result.path}")


@mcp_app.command("setup-claude")
def mcp_setup_claude(
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
    refresh: Annotated[
        bool, typer.Option(help="Replace only an old generated Graphit launcher.")
    ] = False,
) -> None:
    """Opt in to project-local Claude Code MCP configuration."""

    try:
        root = _source_root(project)
        require_store_path(root)
        result = setup_claude(root, refresh=refresh)
    except SourceError as error:
        _source_failure(error)
    except ClaudeSetupError as error:
        typer.echo(f"CLAUDE_SETUP_FAILED: {error}", err=True)
        raise typer.Exit(code=2) from error
    state = "updated" if result.changed else "already configured"
    typer.echo(f"Claude MCP {state}: {result.path}")
    if result.changed:
        typer.echo("Note: .mcp.json contains machine-specific paths; review before committing.")


def _query_failure(error: QueryError) -> None:
    typer.echo(f"{error.code}: {error}", err=True)
    if error.code.startswith("INVALID_"):
        exit_code = 2
    elif error.code in {
        "NO_SNAPSHOT",
        "SNAPSHOT_NOT_FOUND",
        "TABLE_NOT_FOUND",
        "COLUMN_NOT_FOUND",
        "AMBIGUOUS_TABLE",
        "VIEW_NOT_FOUND",
        "AMBIGUOUS_VIEW",
        "INDEX_TARGET_NOT_FOUND",
        "AMBIGUOUS_INDEX_TARGET",
        "PATH_NOT_FOUND",
        "CANDIDATE_NOT_FOUND",
        "SNAPSHOT_CHANGED",
        "REJECTION_NOT_FOUND",
        "APPROVAL_NOT_FOUND",
        "PROPOSAL_NOT_FOUND",
        "PROPOSAL_STALE",
        "PROPOSAL_REVOKED",
        "MANUAL_APPROVAL_NOT_FOUND",
    }:
        exit_code = 6
    elif error.code in {
        "PATH_BUDGET_EXCEEDED",
        "INFERENCE_BUDGET_EXCEEDED",
        "DIFF_BUDGET_EXCEEDED",
        "IMPACT_BUDGET_EXCEEDED",
        "INDEX_BUDGET_EXCEEDED",
    }:
        exit_code = 7
    else:
        exit_code = 1
    raise typer.Exit(code=exit_code) from error


def _column_flags(nullable: bool, primary_key: bool, unique_value: bool) -> str:
    flags = ["NULL" if nullable else "NOT NULL"]
    if primary_key:
        flags.append("PK")
    if unique_value:
        flags.append("UNIQUE")
    return " ".join(flags)


def _foreign_key_flags(validated: bool, target_in_scope: bool, inherited: bool) -> str:
    flags = []
    if not validated:
        flags.append("NOT VALID")
    if not target_in_scope:
        flags.append("target outside scan scope")
    if inherited:
        flags.append("inherited")
    return f" [{'; '.join(flags)}]" if flags else ""


@source_app.command("add")
def source_add(
    name: Annotated[str, typer.Argument(help="Short local source name.")],
    host: Annotated[str, typer.Option(help="PostgreSQL hostname or IP address.")],
    database: Annotated[str, typer.Option(help="Database name.")],
    username: Annotated[str, typer.Option(help="Read-only database username.")],
    credential_env: Annotated[
        str, typer.Option(help="Environment variable NAME containing the password.")
    ],
    port: Annotated[int, typer.Option(help="PostgreSQL port.")] = 5432,
    schema: Annotated[list[str] | None, typer.Option(help="Schema; repeat for more.")] = None,
    ssl_mode: Annotated[str, typer.Option(help="PostgreSQL SSL mode.")] = "prefer",
    engine: Annotated[
        str, typer.Option(help="Source engine; PostgreSQL only for now.")
    ] = "postgresql",
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Save a PostgreSQL source definition; do not connect or read a password."""

    source = SourceConfig(
        name=name,
        engine=engine,
        host=host,
        port=port,
        database_name=database,
        username=username,
        credential_env=credential_env,
        schemas=tuple(schema) if schema else ("public",),
        ssl_mode=ssl_mode,
    )
    try:
        add_source(_source_root(project), source)
    except SourceError as error:
        _source_failure(error)
    typer.echo(f"Added PostgreSQL source '{name}'. No database connection was made.")


@source_app.command("list")
def source_list(
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """List configured sources without showing credentials."""

    try:
        sources = list_sources(_source_root(project))
    except SourceError as error:
        _source_failure(error)
    if not sources:
        typer.echo("No sources configured.")
    for source in sources:
        typer.echo(
            f"{source.name}\t{source.engine}\t{source.host}:{source.port}/{source.database_name}"
        )


@source_app.command("show")
def source_show(
    name: Annotated[str, typer.Argument(help="Configured source name.")],
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show non-secret source metadata and the credential variable name."""

    try:
        source = show_source(_source_root(project), name)
    except SourceError as error:
        _source_failure(error)
    typer.echo(f"Name: {source.name}")
    typer.echo(f"Engine: {source.engine}")
    typer.echo(f"Host: {source.host}")
    typer.echo(f"Port: {source.port}")
    typer.echo(f"Database: {source.database_name}")
    typer.echo(f"Username: {source.username}")
    if source.credential_kind == "url_dotenv":
        typer.echo(f"Credential reference: {source.credential_file} -> {source.credential_env}")
    elif source.credential_kind == "url_env":
        typer.echo(f"Credential reference: environment -> {source.credential_env}")
    else:
        typer.echo(f"Credential environment variable: {source.credential_env}")
    typer.echo(f"Schemas: {', '.join(source.schemas)}")
    typer.echo(f"SSL mode: {source.ssl_mode}")


@source_app.command("test")
def source_test(
    name: Annotated[str, typer.Argument(help="Configured source name.")],
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Verify a PostgreSQL source using one bounded read-only SELECT."""

    try:
        root = _source_root(project)
        source = show_source(root, name)
    except SourceError as error:
        _source_failure(error)
    try:
        result = verify_connection(source, project_root=root)
    except ConnectionTestError as error:
        typer.echo(f"{error.code}: {error}", err=True)
        raise typer.Exit(code=4) from None
    typer.echo(
        f"Connected to PostgreSQL {result.server_version}: "
        f"{result.database} as {result.username} (read-only)."
    )


@app.command()
def scan(
    source: Annotated[str, typer.Option(help="Configured source name.")],
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Read source metadata and save one immutable local snapshot."""

    try:
        result = scan_source(_source_root(project), source)
    except SourceError as error:
        _source_failure(error)
    except MetadataScanError as error:
        typer.echo(f"SCAN_FAILED: {error.code}: {error}", err=True)
        raise typer.Exit(code=5) from None
    except SnapshotError as error:
        typer.echo(f"SCAN_FAILED: {error.code}: {error}", err=True)
        raise typer.Exit(code=5) from None
    typer.echo(
        f"Snapshot {result.version} saved for {result.source_name}: "
        f"{result.object_count} objects, {result.edge_count} edges."
    )


@app.command()
def candidates(
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[int, typer.Option(help=f"Maximum candidates, 1-{MAX_CANDIDATE_LIMIT}.")] = 20,
    include_ambiguous: Annotated[
        bool,
        typer.Option(
            "--include-ambiguous", help="Show up to three unverified target alternatives."
        ),
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Preview unconfirmed relationship candidates from saved metadata only."""

    try:
        result = preview_candidates(
            _source_root(project), source, limit, include_ambiguous=include_ambiguous
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(f"Source {result.source_name}, snapshot {result.snapshot_version}:")
    if result.include_ambiguous:
        typer.echo("Including ambiguous, unverified alternatives for inspection.")
    for candidate in result.candidates:
        typer.echo(
            f"{candidate.status} {candidate.confidence:.2f}\t"
            f"{candidate.source_column} -> {candidate.target_column}"
            f" (alternatives: {candidate.alternatives_for_source})"
        )
    if any(candidate.status == "APPROVED" for candidate in result.candidates):
        typer.echo("APPROVED means human-reviewed logical relation, not a declared FK.")
    if not result.candidates:
        typer.echo("No conservative metadata-only candidates found.")
    if result.truncated:
        typer.echo(f"More candidates exist; refine the limit (max {MAX_CANDIDATE_LIMIT}).")
    if result.skipped_ambiguous_columns:
        typer.echo(f"Skipped {result.skipped_ambiguous_columns} ambiguous columns.")


@review_app.command("reject")
def review_reject(
    source_column: Annotated[str, typer.Argument(help="Exact qualified candidate source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified candidate target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Version shown by candidates.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Reject one current inferred pair; keep declared FKs unchanged."""

    try:
        result = reject_candidate(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Rejected" if result.changed else "Already rejected"
        typer.echo(f"{state}: {result.source_column} -> {result.target_column}")


@review_app.command("propose")
def review_propose(
    source_column: Annotated[str, typer.Argument(help="Exact qualified source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified target key column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Current snapshot version.")],
    reason: Annotated[str, typer.Option(help="Short business reason; do not include secrets.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Record a human proposal; it is not approved or graph-visible."""

    try:
        result = propose_relationship(
            _source_root(project), source, source_column, target_column, snapshot_version, reason
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Proposed" if result.changed else "Already proposed"
        typer.echo(f"{state} (not approved): {result.source_column} -> {result.target_column}")


@review_app.command("proposals")
def review_proposals(
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[int, typer.Option(help="Proposal page size, 1-50.")] = DEFAULT_PROPOSAL_LIMIT,
    offset: Annotated[int, typer.Option(help="Skip this many latest proposals.")] = 0,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """List local manual proposals and their current-snapshot eligibility."""

    try:
        result = list_proposals(_source_root(project), source, limit, offset)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(f"Source {result.source_name}, snapshot {result.snapshot_version}:")
    for proposal in result.proposals:
        typer.echo(
            f"{proposal.status} (manual), {proposal.eligibility}: "
            f"{proposal.source_column} -> {proposal.target_column}"
        )
        typer.echo(f"  Reason: {proposal.reason}")
    if not result.proposals:
        typer.echo("No manual proposals on this page.")
    if result.truncated:
        typer.echo(f"More proposals exist; increase offset (limit max {MAX_PROPOSAL_LIMIT}).")


@review_app.command("approve-proposal")
def review_approve_proposal(
    source_column: Annotated[str, typer.Argument(help="Exact qualified proposed source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified proposed target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Version shown by proposals.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Approve one current manual proposal locally; graph visibility is separate."""

    try:
        result = approve_manual_proposal(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Approved" if result.changed else "Already approved"
        typer.echo(f"{state} manual relation (not a declared FK or graph link):")
        typer.echo(f"{result.source_column} -> {result.target_column}")


@review_app.command("revoke-proposal")
def review_revoke_proposal(
    source_column: Annotated[str, typer.Argument(help="Exact qualified approved source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified approved target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Current snapshot version.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Withdraw a manual approval while preserving local review history."""

    try:
        result = revoke_manual_proposal(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Revoked" if result.changed else "Already revoked"
        typer.echo(f"{state} manual approval: {result.source_column} -> {result.target_column}")


@review_app.command("approve")
def review_approve(
    source_column: Annotated[str, typer.Argument(help="Exact qualified candidate source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified candidate target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Version shown by candidates.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Approve one logical hypothesis; never claim a database-declared FK."""

    try:
        result = approve_candidate(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Approved" if result.changed else "Already approved"
        typer.echo(f"{state} logical relation (not a declared FK): ")
        typer.echo(f"{result.source_column} -> {result.target_column}")


@review_app.command("restore")
def review_restore(
    source_column: Annotated[str, typer.Argument(help="Exact qualified source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Current snapshot version.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Restore one rejected pair while preserving local review history."""

    try:
        result = restore_candidate(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Restored" if result.changed else "Already restored"
        typer.echo(f"{state}: {result.source_column} -> {result.target_column}")


@review_app.command("revoke")
def review_revoke(
    source_column: Annotated[str, typer.Argument(help="Exact qualified source column.")],
    target_column: Annotated[str, typer.Argument(help="Exact qualified target column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    snapshot_version: Annotated[int, typer.Option(help="Current snapshot version.")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Revoke human approval while preserving local review history."""

    try:
        result = revoke_approval(
            _source_root(project), source, source_column, target_column, snapshot_version
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
    else:
        state = "Revoked" if result.changed else "Already revoked"
        typer.echo(f"{state} approval: {result.source_column} -> {result.target_column}")


@app.command("diff")
def snapshot_diff_command(
    source: Annotated[str, typer.Option(help="Configured source name.")],
    from_version: Annotated[int, typer.Option(help="Older completed snapshot version.")],
    to_version: Annotated[int, typer.Option(help="Newer completed snapshot version.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum changes, 1-{MAX_DIFF_LIMIT}.")
    ] = DEFAULT_DIFF_LIMIT,
    offset: Annotated[int, typer.Option(help=f"Changes to skip, 0-{MAX_DIFF_OFFSET}.")] = 0,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Compare saved schema, relation, column, key, and declared FK facts."""

    try:
        result = compare_snapshots(
            _source_root(project), source, from_version, to_version, limit, offset
        )
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"Source {result.source_name}, snapshots {result.from_version} -> "
        f"{result.to_version}: {result.change_count} changes "
        "(schema/relation/column/declared key/FK; reviews and lineage excluded)."
    )
    typer.echo(f"Showing changes from offset {result.offset}.")
    for change in result.changes:
        typer.echo(f"{change.change}\t{change.kind}\t{change.qualified_name}")
        if change.change == "CHANGED":
            typer.echo(
                "  "
                + json.dumps(
                    {"before": change.before, "after": change.after},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    if result.truncated:
        typer.echo(
            f"More changes exist; use --offset {result.offset + len(result.changes)} "
            f"(or increase --limit, max {MAX_DIFF_LIMIT})."
        )


@app.command("snapshots")
def snapshot_list_command(
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum completed snapshots, 1-{MAX_HISTORY_LIMIT}.")
    ] = DEFAULT_HISTORY_LIMIT,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """List completed local snapshot versions for an exact source."""

    try:
        result = list_snapshots(_source_root(project), source, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(f"Source {result.source_name}: {result.snapshot_count} completed snapshots.")
    for snapshot in result.snapshots:
        typer.echo(f"{snapshot.version}\t{snapshot.status}\t{snapshot.completed_at} UTC")
    if result.truncated:
        typer.echo(f"More snapshots exist; increase --limit (max {MAX_HISTORY_LIMIT}).")


@app.command("impact")
def impact_command(
    table: Annotated[str, typer.Argument(help="Exact table name or schema.table.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum declared FK links, 1-{MAX_IMPACT_LIMIT}.")
    ] = DEFAULT_IMPACT_LIMIT,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show direct declared-FK dependents from a saved local snapshot."""

    try:
        result = table_impact(_source_root(project), source, table, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"Direct declared-FK impact for {result.qualified_name} "
        f"(source {result.source_name}, snapshot {result.snapshot_version}): "
        f"{result.dependent_table_count} tables, {result.foreign_key_count} FKs."
    )
    if not result.in_scope:
        typer.echo("Target is outside scan scope; only saved incoming FKs are known.")
    for link in result.dependents:
        pairs = ", ".join(f"{left} -> {right}" for left, right in link.column_pairs)
        typer.echo(f"{link.source_table}\t{link.name}\t{pairs}")
    if result.truncated:
        typer.echo(f"More FK links exist; increase --limit (max {MAX_IMPACT_LIMIT}).")
    typer.echo(
        "Structural FK impact only; application dependencies and inferred links are unknown."
    )


@app.command("impact-tree")
def impact_tree_command(
    table: Annotated[str, typer.Argument(help="Exact table name or schema.table.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    max_hops: Annotated[
        int, typer.Option(help=f"Maximum incoming FK hops, 0-{MAX_PATH_HOPS}.")
    ] = DEFAULT_PATH_HOPS,
    limit: Annotated[
        int, typer.Option(help=f"Maximum dependent tables, 1-{MAX_IMPACT_LIMIT}.")
    ] = DEFAULT_IMPACT_LIMIT,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show bounded direct and indirect declared-FK table reachability."""

    try:
        result = transitive_table_impact(_source_root(project), source, table, max_hops, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"Declared-FK impact tree for {result.qualified_name} "
        f"(source {result.source_name}, snapshot {result.snapshot_version}, "
        f"up to {result.max_hops} hops): {result.dependent_table_count} tables."
    )
    if not result.in_scope:
        typer.echo("Target is outside scan scope; only saved incoming FKs are known.")
    for item in result.dependents:
        route = " <- ".join(step.foreign_key for step in item.steps)
        typer.echo(f"{item.hops}\t{item.dependent_table}\t{route}")
    if result.truncated:
        typer.echo(f"More dependent tables exist; increase --limit (max {MAX_IMPACT_LIMIT}).")
    typer.echo("Structural FK reachability only; application dependencies are unknown.")


@app.command("impact-column")
def impact_column_command(
    column: Annotated[str, typer.Argument(help="Exact table.column or schema.table.column.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum declared FK column pairs, 1-{MAX_IMPACT_LIMIT}.")
    ] = DEFAULT_IMPACT_LIMIT,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show direct declared-FK source columns referencing one saved column."""

    try:
        result = column_impact(_source_root(project), source, column, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"Direct declared-FK column impact for {result.qualified_name} "
        f"(source {result.source_name}, snapshot {result.snapshot_version}): "
        f"{result.reference_count} column pairs."
    )
    for item in result.references:
        typer.echo(
            f"{item.source_column} -> {item.target_column}\t{item.foreign_key} "
            f"(pair {item.pair_position}/{item.pair_count})"
        )
    if result.truncated:
        typer.echo(f"More FK column pairs exist; increase --limit (max {MAX_IMPACT_LIMIT}).")
    typer.echo("Declared FK pairs only; application and transitive impact remain unknown.")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="One or more object-name terms.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[int, typer.Option(help=f"Maximum matches, 1-{MAX_SEARCH_LIMIT}.")] = 20,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Search the latest saved schema graph without contacting PostgreSQL."""

    try:
        result = search_objects(_source_root(project), source, query, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(f"Source {result.source_name}, snapshot {result.snapshot_version}:")
    for match in result.matches:
        detail = ""
        if match.kind == "COLUMN":
            flags = _column_flags(
                bool(match.nullable), bool(match.primary_key), bool(match.unique_value)
            )
            detail = f" : {match.data_type} {flags}"
        elif not match.in_scope:
            detail = " [outside scan scope]"
        typer.echo(f"{match.kind}\t{match.qualified_name}{detail}")
    if not result.matches:
        typer.echo("No matching schema objects.")
    if result.truncated:
        typer.echo(
            f"More matches exist; refine the query or increase --limit (max {MAX_SEARCH_LIMIT})."
        )


@app.command()
def show(
    table: Annotated[str, typer.Argument(help="Exact table or schema.table name.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum items per section, 1-{MAX_SHOW_LIMIT}.")
    ] = 30,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show one table's saved columns, keys, and outgoing foreign keys."""

    try:
        result = show_table(_source_root(project), source, table, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    labels = []
    if not result.in_scope:
        labels.append("outside scan scope")
    if result.partitioned:
        labels.append("partitioned")
    scope = f" [{'; '.join(labels)}]" if labels else ""
    typer.echo(
        f"Table {result.qualified_name}{scope} "
        f"(source {result.source_name}, snapshot {result.snapshot_version})"
    )
    for column in result.columns:
        typer.echo(
            f"COLUMN\t{column.name} : {column.data_type} "
            f"{_column_flags(column.nullable, column.primary_key, column.unique_value)}"
        )
    for key in result.keys:
        inherited = " [inherited]" if key.inherited else ""
        typer.echo(f"{key.kind}\t{key.name} ({', '.join(key.columns)}){inherited}")
    for foreign_key in result.foreign_keys:
        pairs = ", ".join(f"{source} -> {target}" for source, target in foreign_key.column_pairs)
        suffix = _foreign_key_flags(
            foreign_key.validated, foreign_key.target_in_scope, foreign_key.inherited
        )
        typer.echo(
            f"FOREIGN_KEY\t{foreign_key.name} ({pairs}) -> {foreign_key.target_table}{suffix}"
        )
    for kind, truncated in (
        ("columns", result.columns_truncated),
        ("keys", result.keys_truncated),
        ("foreign keys", result.foreign_keys_truncated),
    ):
        if truncated:
            typer.echo(f"More {kind} exist; increase --limit (max {MAX_SHOW_LIMIT}).")


@app.command("show-view")
def show_view_command(
    view: Annotated[str, typer.Argument(help="Exact view or schema.view name.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[int, typer.Option(help=f"Maximum columns, 1-{MAX_SHOW_LIMIT}.")] = 30,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show saved view columns, without a database connection or lineage claims."""

    try:
        result = show_view(_source_root(project), source, view, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"{result.kind} {result.qualified_name} "
        f"(source {result.source_name}, snapshot {result.snapshot_version})"
    )
    for column in result.columns:
        nullability = (
            "NOT NULL declared" if column.not_null_declared else "nullability not declared"
        )
        typer.echo(f"COLUMN\t{column.name} : {column.data_type} [{nullability}]")
    if result.columns_truncated:
        typer.echo(f"More columns exist; increase --limit (max {MAX_SHOW_LIMIT}).")


@app.command("indexes")
def indexes_command(
    relation: Annotated[
        str, typer.Argument(help="Exact table/materialized-view or schema-qualified name.")
    ],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum indexes per page, 1-{MAX_INDEX_LIMIT}.")
    ] = DEFAULT_INDEX_LIMIT,
    offset: Annotated[int, typer.Option(help=f"Zero-based page offset, 0-{MAX_INDEX_OFFSET}.")] = 0,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """List saved indexes for one relation without reading the source database."""

    try:
        result = list_table_indexes(_source_root(project), source, relation, limit, offset)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    typer.echo(
        f"{result.relation_kind} {result.qualified_name} "
        f"(source {result.source_name}, snapshot {result.snapshot_version}): "
        f"{result.index_count} indexes"
    )
    for index in result.indexes:
        keys = ", ".join(
            f"{part.position}:{part.column_name or '<expression>'}" for part in index.key_columns
        )
        included = ", ".join(
            f"{part.position}:{part.column_name}" for part in index.included_columns
        )
        flags = [
            label
            for enabled, label in (
                (index.unique, "UNIQUE"),
                (index.primary, "PRIMARY"),
                (not index.valid, "INVALID"),
                (not index.ready, "NOT READY"),
                (index.partial, "PARTIAL"),
            )
            if enabled
        ]
        suffix = f" [{', '.join(flags)}]" if flags else ""
        typer.echo(f"INDEX\t{index.name} USING {index.access_method} ({keys}){suffix}")
        if included:
            typer.echo(f"\tINCLUDE ({included})")
    if result.truncated:
        typer.echo(
            f"More indexes exist; use --offset {result.offset + len(result.indexes)} "
            f"(or increase --limit, max {MAX_INDEX_LIMIT})."
        )


@app.command()
def relationships(
    table: Annotated[str, typer.Argument(help="Exact table or schema.table name.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    limit: Annotated[
        int, typer.Option(help=f"Maximum FKs per direction, 1-{MAX_RELATIONSHIP_LIMIT}.")
    ] = 20,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Show direct declared foreign keys into and out of one saved table."""

    try:
        result = table_relationships(_source_root(project), source, table, limit)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    scope = " [outside scan scope]" if not result.in_scope else ""
    typer.echo(
        f"Declared FKs for {result.qualified_name}{scope} "
        f"(source {result.source_name}, snapshot {result.snapshot_version}):"
    )
    for direction, items in (("IN", result.incoming), ("OUT", result.outgoing)):
        for relation in items:
            pairs = ", ".join(f"{left} -> {right}" for left, right in relation.column_pairs)
            suffix = _foreign_key_flags(
                relation.validated, relation.target_in_scope, relation.inherited
            )
            typer.echo(
                f"{direction}\t{relation.name}: {relation.source_table} "
                f"({pairs}) -> {relation.target_table}{suffix}"
            )
    if not result.incoming and not result.outgoing:
        typer.echo("No declared FK relationships in this snapshot.")
    if result.incoming_truncated:
        typer.echo(f"More incoming FKs exist; increase --limit (max {MAX_RELATIONSHIP_LIMIT}).")
    if result.outgoing_truncated:
        typer.echo(f"More outgoing FKs exist; increase --limit (max {MAX_RELATIONSHIP_LIMIT}).")


@app.command()
def graph(
    table: Annotated[str, typer.Argument(help="Exact focus table or schema.table name.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    output_format: Annotated[
        Literal["json", "dot", "html"], typer.Option("--format", help="Output format.")
    ] = "json",
    output: Annotated[
        Path | None, typer.Option("--output", help="Create a new output file; never overwrite.")
    ] = None,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Emit a one-hop JSON, DOT, or offline HTML graph."""

    try:
        result = table_graph(_source_root(project), source, table, include_manual=True)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if output_format == "dot":
        content = render_dot(result)
    elif output_format == "html":
        content = render_html(result)
    else:
        content = json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")) + "\n"
    if output is None:
        typer.echo(content, nl=False)
        return
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except FileExistsError as error:
        typer.echo(f"OUTPUT_EXISTS: {output}", err=True)
        raise typer.Exit(code=2) from error
    except OSError as error:
        typer.echo(f"OUTPUT_WRITE_FAILED: {output}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Created graph: {output}")


@app.command()
def path(
    from_table: Annotated[str, typer.Argument(help="Starting exact table name.")],
    to_table: Annotated[str, typer.Argument(help="Destination exact table name.")],
    source: Annotated[str, typer.Option(help="Configured source name.")],
    max_hops: Annotated[int, typer.Option(help=f"Maximum FK steps, 0-{MAX_PATH_HOPS}.")] = 4,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable JSON.")
    ] = False,
    project: Annotated[Path | None, typer.Option(help="Graphit project directory.")] = None,
) -> None:
    """Find one bounded shortest path through declared foreign keys."""

    try:
        result = find_table_path(_source_root(project), source, from_table, to_table, max_hops)
    except SourceError as error:
        _source_failure(error)
    except QueryError as error:
        _query_failure(error)
    if json_output:
        typer.echo(json.dumps(asdict(result), ensure_ascii=False, separators=(",", ":")))
        return
    route = f"{result.start_table} -> {result.end_table}"
    context = (
        f"{len(result.steps)} hops, source {result.source_name}, snapshot {result.snapshot_version}"
    )
    typer.echo(f"Declared FK path: {route} ({context})")
    if not result.steps:
        typer.echo("Same table; no FK traversal needed.")
    for index, step in enumerate(result.steps, start=1):
        pairs = ", ".join(f"{left} -> {right}" for left, right in step.column_pairs)
        suffix = _foreign_key_flags(step.validated, step.target_in_scope, step.inherited)
        typer.echo(
            f"{index}. {step.from_table} --{step.foreign_key} "
            f"[{pairs}; {step.direction}]--> {step.to_table}{suffix}"
        )
