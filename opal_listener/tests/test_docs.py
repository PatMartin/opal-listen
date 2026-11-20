"""Documentation conformance: every `opal listen ...` command and `/api/v1/...` route mentioned in
the docs must resolve against the live implementation.

CLI commands are checked by walking the Typer command tree; the longest token prefix must match a
real group/command (trailing tokens are positional arguments). REST routes are checked
segment-wise against the generated OpenAPI path set — a documented route matches when it has the
same segment count as a spec path and every segment either equals the spec literal or falls on a
spec `{param}` position (concrete ids/keys in examples are fine).

Exclusions, kept deliberately narrow:
- Lines naming a foreign port (:8080, :8001, …) or a sibling service (opal-event, opal-say, …) —
  those routes belong to other OPAL contracts.
- Mermaid diagram blocks — `opal listen CLI` is a node label, not a command.
- Lines marked historical (`retired`/`removed`/`replaces`/`no longer`) — changelog prose may name
  dead routes.
- Routes containing `..` — ellipsized placeholders.
"""

from __future__ import annotations

import re

from typer.main import get_command

from opal_listener.app_settings import PACKAGE_DIR
from opal_listener.cli.main import app
from opal_listener.main import create_app

REPO_ROOT = PACKAGE_DIR.parent
DOC_FILES = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]

_CLI_RE = re.compile(r"(?<![.\w])opal listen ([a-zA-Z][\w|-]*)((?:\s+[a-zA-Z][\w|-]*)*)")
_METHOD_ROUTE_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/api/v1/[^\s`)*,]+)")
_BARE_ROUTE_RE = re.compile(r"(/api/v1/[^\s`)*,\]\"'<>]+)")
_FOREIGN_RE = re.compile(r":(?!8000\b)\d{4,5}\b|\bopal-(event|say|see|think|brain|cam|device)\b", re.I)
_HISTORICAL_RE = re.compile(r"\b(retired|removed|replaces|no longer)\b", re.I)


def _command_groups() -> set[tuple[str, ...]]:
    def walk(group, prefix):
        for name, cmd in group.commands.items():
            p = prefix + (name,)
            yield p
            if hasattr(cmd, "commands"):
                yield from walk(cmd, p)

    paths = {p for p in walk(get_command(app), ()) if p and p[0] == "listen"}
    leaves = {p[1:] for p in paths}
    return leaves | {p[:i] for p in leaves for i in range(1, len(p))}


_GROUPS = _command_groups()


def _spec_segments() -> list[list[str]]:
    """Spec paths as segment lists; `{param}` positions stored as '{}'."""
    out = []
    for path in create_app().openapi()["paths"]:
        segs = ["{}" if s.startswith("{") else s for s in path.strip("/").split("/") if s]
        out.append(segs)
    return out


_SPEC_SEGMENTS = _spec_segments()


def _route_in_spec(route: str) -> bool:
    if ".." in route:
        return True  # ellipsized placeholder
    segs = [s for s in route.split("?")[0].rstrip(".,;:").strip("/").split("/") if s]
    return any(
        # exact route (param positions accept any concrete value) or a route-family prefix
        spec[: len(segs)] == segs
        or (len(spec) == len(segs) and all(s == "{}" or s == d for s, d in zip(spec, segs, strict=True)))
        for spec in _SPEC_SEGMENTS
    )


def _iter_doc_lines():
    """Yield (file, lineno, line) skipping fenced mermaid blocks."""
    for path in DOC_FILES:
        in_mermaid = False
        for ln, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("```"):
                in_mermaid = stripped == "```mermaid" if not in_mermaid else False
                continue
            if not in_mermaid:
                yield path, ln, line


def _cli_hits() -> list[tuple[str, int, str]]:
    bad = []
    for path, ln, line in _iter_doc_lines():
        for m in _CLI_RE.finditer(line):
            toks = (m.group(1) + (m.group(2) or "")).split()
            i = 0
            while i < len(toks) and tuple(toks[: i + 1]) in _GROUPS:
                i += 1
            if i == 0:
                bad.append((str(path.relative_to(REPO_ROOT)), ln, " ".join(toks)))
    return bad


def _route_hits() -> list[tuple[str, int, str]]:
    bad = []
    for path, ln, line in _iter_doc_lines():
        if _FOREIGN_RE.search(line) or _HISTORICAL_RE.search(line):
            continue
        covered = set()
        for m in _METHOD_ROUTE_RE.finditer(line):
            covered.add(m.span(2))
            if not _route_in_spec(m.group(2)):
                bad.append((str(path.relative_to(REPO_ROOT)), ln, m.group(2)))
        for m in _BARE_ROUTE_RE.finditer(line):
            if m.span(1) not in covered and not _route_in_spec(m.group(1)):
                bad.append((str(path.relative_to(REPO_ROOT)), ln, m.group(1)))
    return bad


def test_all_doc_files_present():
    assert len(DOC_FILES) >= 5, [str(p) for p in DOC_FILES]


def test_documented_cli_commands_resolve():
    bad = _cli_hits()
    assert not bad, "documented commands with no matching CLI group/command:\n" + "\n".join(
        f"  {f}:{ln}: opal listen {cmd}" for f, ln, cmd in bad
    )


def test_documented_routes_resolve():
    bad = _route_hits()
    assert not bad, "documented routes missing from the generated OpenAPI:\n" + "\n".join(
        f"  {f}:{ln}: {route}" for f, ln, route in bad
    )


def test_doc_validator_sanity():
    """Guard the validator itself: known-good and known-dead surfaces."""
    assert ("service", "health") in _GROUPS
    assert ("info",) not in _GROUPS
    assert ("recognition",) not in _GROUPS
    assert _route_in_spec("/api/v1/health/")
    assert _route_in_spec("/api/v1/setting/cooldown_ms")  # concrete key on a {key} route
    assert _route_in_spec("/api/v1/wakeword/2")  # concrete id
    assert not _route_in_spec("/api/v1/health/audio_devices")
    assert not _route_in_spec("/api/v1/regression/")
