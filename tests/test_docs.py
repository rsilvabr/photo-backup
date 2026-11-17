import ast
import re
from pathlib import Path

import captureone_backup
import manifest_backup
import restore_backup
import selective_backup

REPO = Path(__file__).resolve().parents[1]
# The pages that say how the tool works today: the README and docs/, without
# the release notes (they name what earlier versions did).
HISTORY_PAGES = {"version_history.md", "whats_new.md"}
# Constants at the top of a script that are not settings: categories,
# statuses, labels, schema. Everything else there must be in its docs.
NOT_SETTINGS = {
    "selective_backup.py": {"CAT_EXPORT", "CAT_SOURCE", "CAT_UNSELECTED", "CAT_CAPTURE_ONE",
                            "CAT_NX_STUDIO", "CAT_UNEXPORTED", "CAT_ADDED", "CAT_WHOLE",
                            "ST_NEW", "ST_CHANGED", "ST_UP_TO_DATE", "ST_MOVED",
                            "SCHEMA_VERSION"},
    "restore_backup.py": {"GOOD"},
    "photo_backup.py": {"KIND_BACKUP", "KIND_VERIFY"},
    "manifest_backup.py": set(),
    "captureone_backup.py": {"LABEL_USER", "LABEL_PREFERENCES", "LABEL_INSTALLED",
                             "LABEL_WINDOWS", "PROTECTED_LABELS", "BAD_RESTORE", "BAD_REVERT"},
    "mobile_backup.py": set(),
}


def _top_settings(script):
    """The UPPER_CASE names a script assigns before its first function or
    class: the settings block at its top."""
    names = []
    for node in ast.parse((REPO / script).read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            break
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        for target in targets:
            if isinstance(target, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", target.id):
                names.append(target.id)
    return names


def _docs_text():
    """README.md and every page of docs/ but the release notes, as one text."""
    pages = [REPO / "README.md"] + sorted(
        page for page in (REPO / "docs").glob("*.md") if page.name not in HISTORY_PAGES)
    return "\n".join(page.read_text(encoding="utf-8") for page in pages)


def test_docs_cover_the_settings():
    """Every setting at the top of a script is in its docs (D1 of the
    261112 sweep: the menu's and Capture One's were not)."""
    docs = _docs_text()
    mobile_readme = (REPO / "README_mobile_backup.md").read_text(encoding="utf-8")
    for script, internal in NOT_SETTINGS.items():
        text = mobile_readme if script == "mobile_backup.py" else docs
        names = _top_settings(script)
        assert names, f"no settings found at the top of {script}"
        missing = [name for name in names if name not in internal and f"`{name}`" not in text]
        assert not missing, f"{script}: settings missing from its docs: {missing}"


def test_docs_cover_the_flags():
    docs = _docs_text()
    for parser in (selective_backup._build_parser(), restore_backup._build_parser(),
                   captureone_backup.build_parser(), manifest_backup.build_parser()):
        for action in parser._actions:
            for option in action.option_strings:
                if option.startswith("--"):
                    assert option in docs, f"{option} is missing from README.md and docs/"
    for text in ("restore_backup.py", "[5] Restore", "captureone_backup.py",
                 "[6] Capture One only", "manifest_backup.py", "[7] Manifest"):
        assert text in docs, f"{text} is missing from README.md and docs/"


_FENCE = re.compile(r"\s*(```|~~~)")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _slug(heading):
    """The anchor GitHub gives a heading: lower case, a link keeps its text,
    punctuation dropped (not `-` or `_`), spaces to `-`."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors_and_links(page):
    anchors, links, seen, fenced = set(), [], {}, False
    for line in page.read_text(encoding="utf-8").splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = re.match(r"#{1,6}\s+(.*?)\s*$", line)
        if heading:
            slug = _slug(heading.group(1))
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchors.add(slug if count == 0 else f"{slug}-{count}")
        links += _LINK.findall(re.sub(r"`[^`]*`", "", line))
    return anchors, links


def test_docs_links_resolve():
    """Every relative link of the README, the mobile README and docs/ leads
    to a file that exists and, with a `#`, to a heading of that page (the
    README was split into docs/ pages: a moved section must not leave a dead
    link behind)."""
    pages = [REPO / "README.md", REPO / "README_mobile_backup.md",
             *sorted((REPO / "docs").glob("*.md"))]
    broken = []
    for page in pages:
        for target in _anchors_and_links(page)[1]:
            if re.match(r"[a-z]+:", target):    # https:, mailto:
                continue
            path, _, anchor = target.partition("#")
            dest = (page.parent / path).resolve() if path else page
            if not dest.exists():
                broken.append(f"{page.name}: {target} (no such file)")
            elif anchor and dest.suffix == ".md" and anchor not in _anchors_and_links(dest)[0]:
                broken.append(f"{page.name}: {target} (no such heading)")
    assert not broken, broken
