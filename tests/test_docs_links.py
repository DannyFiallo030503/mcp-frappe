"""Every relative link in the docs, and every link into this repository's GitHub tree,
must point at a file that exists (and at a heading that exists, when it has an anchor)."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_URL = re.compile(
    r"^https://github\.com/DannyFiallo030503/mcp-frappe/(?:blob|tree)/main/(?P<path>[^#?]*)"
    r"(?:#(?P<anchor>.*))?$"
)
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
FENCE = re.compile(r"^(```|~~~)")

# The sdist ships tests/ and README.md but not docs/; there is nothing to check there.
pytestmark = pytest.mark.skipif(not (ROOT / "docs").is_dir(), reason="docs/ not present")


def _documents() -> list[Path]:
    skipped = {"legacy", ".venv", ".worktrees", "node_modules", "dist", ".git"}
    files = [path for path in [ROOT / "llms.txt"] if path.is_file()]
    files += [
        path
        for path in ROOT.rglob("*.md")
        if not skipped.intersection(path.relative_to(ROOT).parts)
    ]
    return sorted(files)


def _links(text: str) -> list[str]:
    links: list[str] = []
    fenced = False
    for line in text.splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            links += LINK.findall(re.sub(r"`[^`]*`", "", line))
    return links


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"`|\*\*|__", "", heading.strip().lower())
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    seen: dict[str, int] = {}
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if FENCE.match(line.strip()):
            fenced = not fenced
            continue
        match = None if fenced else re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            slug = _slug(match.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def _target(document: Path, link: str) -> tuple[Path, str | None] | None:
    repo = REPO_URL.match(link)
    if repo:
        return ROOT / unquote(repo.group("path")), repo.group("anchor")
    if re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I):
        return None  # another site, mailto:, ...
    path, _, anchor = link.partition("#")
    base = document if not path else document.parent / unquote(path)
    return base, anchor or None


def _broken(document: Path) -> list[str]:
    broken = []
    for link in _links(document.read_text(encoding="utf-8")):
        target = _target(document, link)
        if target is None:
            continue
        path, anchor = target
        if not path.resolve().is_relative_to(ROOT) or not path.exists():
            broken.append(f"{link} (no such file)")
        elif anchor and path.suffix == ".md" and anchor not in _anchors(path):
            broken.append(f"{link} (no such heading)")
    return broken


@pytest.mark.parametrize("document", _documents(), ids=lambda path: str(path.relative_to(ROOT)))
def test_links_resolve(document: Path) -> None:
    assert _broken(document) == []


def test_readme_links_are_absolute() -> None:
    """The README is also the PyPI page, where relative links don't resolve."""
    relative = [
        link
        for link in _links((ROOT / "README.md").read_text(encoding="utf-8"))
        if not re.match(r"^[a-z][a-z0-9+.-]*:", link, re.I) and not link.startswith("#")
    ]
    assert relative == []


def test_slug_matches_github() -> None:
    assert _slug("Toolsets, read-only mode, and annotations") == (
        "toolsets-read-only-mode-and-annotations"
    )
    assert _slug("`upsert_workflow`") == "upsert_workflow"
    assert _slug("Code fields and templates") == "code-fields-and-templates"
