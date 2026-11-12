import ast
import re
from pathlib import Path

import captureone_backup
import manifest_backup
import restore_backup
import selective_backup

REPO = Path(__file__).resolve().parents[1]
# Constants at the top of a script that are not settings: categories,
# statuses, labels, schema. Everything else there must be in its README.
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


def test_docs_cover_the_settings():
    """Every setting at the top of a script is in its README (D1 of the
    261112 sweep: the menu's and Capture One's were not)."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    mobile_readme = (REPO / "README_mobile_backup.md").read_text(encoding="utf-8")
    for script, internal in NOT_SETTINGS.items():
        text = mobile_readme if script == "mobile_backup.py" else readme
        names = _top_settings(script)
        assert names, f"no settings found at the top of {script}"
        missing = [name for name in names if name not in internal and f"`{name}`" not in text]
        assert not missing, f"{script}: settings missing from its README: {missing}"


def test_docs_cover_the_flags():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for parser in (selective_backup._build_parser(), restore_backup._build_parser(),
                   captureone_backup.build_parser(), manifest_backup.build_parser()):
        for action in parser._actions:
            for option in action.option_strings:
                if option.startswith("--"):
                    assert option in readme, f"{option} is missing from README.md"
    for text in ("restore_backup.py", "[5] Restore", "captureone_backup.py",
                 "[6] Capture One only", "manifest_backup.py", "[7] Manifest"):
        assert text in readme, f"{text} is missing from README.md"
