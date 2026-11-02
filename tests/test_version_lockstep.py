"""Single version-lockstep guard — one authoritative source, every surface derived.

``VERSION`` is the authority. Every other version surface the project publishes
must equal it: the package constant, the CLI banner, the packaging metadata, the
badge and roadmap line of *both* README locales, all three ``web/site.json``
version strings, the demo record the site reads, and the CHANGELOG head plus its
link references.

This replaces the four hardcoded copies that lived in ``tests/test_v030.py`` …
``test_v060.py``: each had to be edited on every release (four unrelated files)
and still did not cover ``pyproject.toml``, the READMEs, the site footer, the
demo record or the CHANGELOG refs. Deriving the expectation from ``VERSION``
means the next bump needs no test edit at all.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

import duanhui
from duanhui.cli import app

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_URL = "https://github.com/SuperMarioYL/duanhui/releases/tag"


def _expected_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _readme(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def test_version_file_is_x_y_z() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _expected_version()), (
        f"VERSION is {_expected_version()!r}, expected MAJOR.MINOR.PATCH"
    )


def test_package_constant_and_cli_banner_match_version_file() -> None:
    expected = _expected_version()
    assert duanhui.__version__ == expected, f"__version__ is {duanhui.__version__!r}"

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stdout
    assert expected in result.stdout, f"CLI version output: {result.stdout!r}"


def test_packaging_metadata_matches_version_file() -> None:
    expected = _expected_version()
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == expected, (
        f"pyproject.toml version is {pyproject['project']['version']!r}"
    )


def test_both_readme_locales_match_version_file() -> None:
    expected = _expected_version()
    for name in ("README.md", "README.en.md"):
        text = _readme(name)
        assert f"`v{expected}`" in text, f"{name} badge is not v{expected}"
        # the roadmap paragraph opens with the released version in both locales
        assert f"\nv{expected} " in text, f"{name} roadmap paragraph is not v{expected}"


def test_site_json_version_strings_match_version_file() -> None:
    expected = _expected_version()
    site = json.loads((REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8"))
    assert site["meta"]["content_version"] == expected, (
        f"site.json meta.content_version is {site['meta']['content_version']!r}"
    )
    assert site["content_version"] == expected, (
        f"site.json content_version is {site['content_version']!r}"
    )
    assert site["footer"]["tag"].startswith(f"{expected} "), (
        f"site.json footer.tag is {site['footer']['tag']!r}"
    )


def test_demo_record_version_matches_version_file() -> None:
    expected = _expected_version()
    record = json.loads(
        (REPO_ROOT / "docs" / "demo-results.json").read_text(encoding="utf-8")
    )
    assert record["version"] == expected, (
        f"docs/demo-results.json version is {record['version']!r}"
    )


def test_changelog_head_and_link_refs_match_version_file() -> None:
    expected = _expected_version()
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## [{expected}]" in changelog, f"CHANGELOG head missing v{expected} entry"
    sections = re.findall(r"^## \[(\d+\.\d+\.\d+)\]$", changelog, re.MULTILINE)
    assert sections == sorted(sections, reverse=True), (
        f"CHANGELOG sections are not newest-first: {sections}"
    )

    # every released section must have a link reference (a heading without one
    # renders as plain text) and the compare range must start at this release.
    for version in sections:
        assert re.search(
            rf"^\[{re.escape(version)}\]: ", changelog, re.MULTILINE
        ), f"CHANGELOG is missing the [{version}] link reference"
    assert re.search(
        rf"^\[Unreleased\]: .*compare/v{re.escape(expected)}\.\.\.HEAD$",
        changelog,
        re.MULTILINE,
    ), "[Unreleased] compare range does not start at the released tag"

    assert f"[{expected}]: {RELEASE_URL}/v{expected}" in changelog
