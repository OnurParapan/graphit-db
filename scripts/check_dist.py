"""Validate local Graphit release archives before installation or publication."""

from __future__ import annotations

import configparser
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

PRIVATE_PARTS = {".wolf", ".codex", ".claude", ".venv", ".graphit"}


def archive_parts(name: str) -> tuple[str, ...]:
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "\\" in name or ".." in parts or PRIVATE_PARTS.intersection(parts):
        raise ValueError(f"Unsafe or private archive member: {name}")
    return parts


def check_distributions(project_root: Path, dist_dir: Path) -> None:
    project_data = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = project_data["project"]
    name = str(project["name"])
    version = str(project["version"])
    authors = project.get("authors")
    if not isinstance(authors, list) or len(authors) != 1 or not isinstance(authors[0], dict):
        raise ValueError("Project metadata must name exactly one copyright author")
    author = str(authors[0].get("name", "")).strip()
    if not author:
        raise ValueError("Project copyright author name is missing")
    raw_urls = project.get("urls")
    if not isinstance(raw_urls, dict) or not raw_urls:
        raise ValueError("Project metadata must declare public project URLs")
    project_urls = {str(label): str(url) for label, url in raw_urls.items()}
    stem = f"{name.replace('-', '_')}-{version}"
    license_bytes = (project_root / "LICENSE").read_bytes()
    repository_notice_path = project_root / "NOTICE"
    if not repository_notice_path.is_file():
        raise ValueError("Repository NOTICE file is missing")
    notice_bytes = repository_notice_path.read_bytes()
    sdist_path = dist_dir / f"{stem}.tar.gz"
    wheel_path = dist_dir / f"{stem}-py3-none-any.whl"

    with tarfile.open(sdist_path, "r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        if len(names) != len(members):
            raise ValueError("Source archive contains duplicate paths")
        allowed = {
            "src",
            "tests",
            "docs",
            "README.md",
            "LICENSE",
            "NOTICE",
            "pyproject.toml",
        }
        generated = {".gitignore", "PKG-INFO"}
        for member in members:
            parts = archive_parts(member.name)
            if len(parts) < 2 or parts[0] != stem or parts[1] not in allowed | generated:
                raise ValueError(f"Unexpected source archive member: {member.name}")
            if not member.isfile():
                raise ValueError(f"Non-file source archive member: {member.name}")
        required = {
            f"{stem}/{item}" for item in ("LICENSE", "NOTICE", "README.md", "pyproject.toml")
        }
        if not required <= names or f"{stem}/src/graphit/cli.py" not in names:
            raise ValueError("Source archive is missing required files")
        license_member = archive.extractfile(f"{stem}/LICENSE")
        if license_member is None or license_member.read() != license_bytes:
            raise ValueError("Source archive license differs from repository LICENSE")
        notice_member = archive.extractfile(f"{stem}/NOTICE")
        if notice_member is None or notice_member.read() != notice_bytes:
            raise ValueError("Source archive notice differs from repository NOTICE")

    with zipfile.ZipFile(wheel_path) as archive:
        wheel_names = archive.namelist()
        if len(wheel_names) != len(set(wheel_names)):
            raise ValueError("Wheel contains duplicate paths")
        metadata_dir = f"{stem}.dist-info"
        for name_in_archive in wheel_names:
            parts = archive_parts(name_in_archive)
            if not parts or parts[0] not in {"graphit", metadata_dir}:
                raise ValueError(f"Unexpected wheel member: {name_in_archive}")
        metadata_path = f"{metadata_dir}/METADATA"
        entry_points_path = f"{metadata_dir}/entry_points.txt"
        license_path = f"{metadata_dir}/licenses/LICENSE"
        wheel_notice_path = f"{metadata_dir}/licenses/NOTICE"
        required_wheel = {
            "graphit/__init__.py",
            "graphit/cli.py",
            metadata_path,
            entry_points_path,
            license_path,
            wheel_notice_path,
        }
        if not required_wheel <= set(wheel_names):
            raise ValueError("Wheel is missing required package or metadata files")
        metadata = Parser().parsestr(archive.read(metadata_path).decode("utf-8"))
        if metadata["Name"] != name or metadata["Version"] != version:
            raise ValueError("Wheel package name or version differs from pyproject.toml")
        if metadata["Author"] != author:
            raise ValueError("Wheel author differs from pyproject.toml")
        expected_project_urls = sorted(f"{label}, {url}" for label, url in project_urls.items())
        if sorted(metadata.get_all("Project-URL", [])) != expected_project_urls:
            raise ValueError("Wheel project URLs differ from pyproject.toml")
        if metadata["License-Expression"] != "Apache-2.0":
            raise ValueError("Wheel Apache-2.0 license expression is missing")
        if metadata.get_all("License-File") != ["LICENSE", "NOTICE"]:
            raise ValueError("Wheel license file metadata is missing")
        if archive.read(license_path) != license_bytes:
            raise ValueError("Wheel license differs from repository LICENSE")
        if archive.read(wheel_notice_path) != notice_bytes:
            raise ValueError("Wheel notice differs from repository NOTICE")
        entry_points = configparser.ConfigParser()
        entry_points.read_string(archive.read(entry_points_path).decode("utf-8"))
        if entry_points.get("console_scripts", "graphit", fallback=None) != "graphit.cli:app":
            raise ValueError("Wheel graphit CLI entry point is missing")


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    check_distributions(root, root / "dist")
    print("Source and wheel distribution checks passed")
