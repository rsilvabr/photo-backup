from pathlib import Path

import selective_backup


def test_docs_cover_the_flags():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    parser = selective_backup._build_parser()
    for action in parser._actions:
        for option in action.option_strings:
            if option.startswith("--"):
                assert option in readme, f"{option} is missing from README.md"
