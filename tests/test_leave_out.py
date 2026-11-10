"""v0.13.0: a session leaves out only the subfolders its mode names (LeaveOut).

Until v0.12.1 every subfolder of a session that was not an export, CaptureOne
or NKSC_PARAM folder stayed out ("inside sessions") unless the mode added it.
Now the modes table names them (LeaveOut); any other subfolder is judged alone,
as the subfolder of a folder with an export and no original beside it already
was. The tests marked "guard" pass on v0.12.1 too and only keep the change from
copying less than before."""
import csv
import io
import os
from pathlib import Path

import pytest

import manifest_backup as mb
import selective_backup as sb

from conftest import latest_csv, make_tree
from test_bug_sweep import CHAINS, SHIPPED_MODES, listed, plan_rows, planned_set
from test_bug_sweep_round2 import full_tree

ALL_MODES = ("1", "2", "3", "4", "5", "6", "1+", "2+", "3+", "4+", "5+")

# backup_modes.csv of v0.12.1, word for word: a table written before LeaveOut
V0121_MODES = """\
Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Only,Skip,Description
1,no,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,,,Exports and the originals of the exported photos
1+,no,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,yes,,,Mode 1 where there is an export; the whole folder where there is none
2,yes,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,,,Mode 1 + unselected originals
2+,yes,,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,yes,,,Mode 2 where there is an export; the whole folder where there is none
3,yes,_OUTTAKES_KEEP,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,,,Mode 2 + _OUTTAKES_KEEP
3+,yes,_OUTTAKES_KEEP,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,yes,,,Mode 3 where there is an export; the whole folder where there is none
4,yes,_OUTTAKES_KEEP;_OUTTAKES,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,,,Mode 3 + _OUTTAKES
4+,yes,_OUTTAKES_KEEP;_OUTTAKES,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,yes,,,Mode 4 where there is an export; the whole folder where there is none
5,yes,_OUTTAKES*,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,no,,,Mode 4 + every _OUTTAKES*
5+,yes,_OUTTAKES*,RAW SCANS*;RAW TIFF*;RAW GAMMA*,,no,yes,,,Mode 5 where there is an export; the whole folder where there is none
6,no,,,,yes,no,,,The whole folder
raw-only,no,,,,yes,no,raw;.xmp;.dop;.pp3;.cos;.comask;.nksc,no,Only the RAW files of the folder and their sidecars and edit settings
jpeg-only,no,,,,yes,no,jpeg,no,Only the JPEG files of the folder
jxl-only,no,,,,yes,no,jxl,no,Only the JXL files of the folder
skip,no,,,,no,no,,yes,This folder stays out of the backup
"""


def v0121_table(tmp_path):
    path = tmp_path / "modes_v0121.csv"
    path.write_text(V0121_MODES, encoding="utf-8")
    return path


def star_table(tmp_path):
    """The v0.12.1 table with LeaveOut = * where a mode walks sessions."""
    rows = list(csv.reader(io.StringIO(V0121_MODES)))
    header = rows[0]
    whole, skip = header.index("Whole"), header.index("Skip")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header[:-1] + ["LeaveOut", header[-1]])
    for row in rows[1:]:
        leave_out = "" if "yes" in (row[whole], row[skip]) else "*"
        writer.writerow(row[:-1] + [leave_out, row[-1]])
    path = tmp_path / "modes_star.csv"
    path.write_text(out.getvalue(), encoding="utf-8")
    return path


# ---- the case that started it (invented names) -------------------------------------
def party_tree(root):
    """A session (one loose original, its export) and, beside the export, a
    subfolder with the photos of another event."""
    make_tree(root, {
        "2026/260321_Party/chat_0001.jpeg": 11,
        "2026/260321_Party/_EXPORT/chat_0001_v1.jpg": 10,
        "2026/260321_Party/Another event/IMG_1.JPG": 5000,
        "2026/260321_Party/Another event/IMG_2.JPG": 5001,
        "2026/260321_Party/Another event/more/IMG_3.JPG": 70,
        "2026/260321_Party/_OUTTAKES/a.NEF": 300,
    })
    return root / "2026"


def party_photos(folder):
    event = folder / "260321_Party" / "Another event"
    return {os.path.normcase(str(event / rel))
            for rel in ("IMG_1.JPG", "IMG_2.JPG", "more/IMG_3.JPG")}


def test_plus_mode_copies_a_session_subfolder_the_mode_does_not_name(tmp_path):
    folder = party_tree(tmp_path / "G")
    plan = plan_rows([(folder, "3+", "raw")], tmp_path)
    by_src = {os.path.normcase(item.src): item.category for item in plan.files}
    for path in party_photos(folder):
        assert by_src.get(path) == sb.CAT_UNEXPORTED, path
    outtakes = folder / "260321_Party" / "_OUTTAKES" / "a.NEF"
    assert os.path.normcase(str(outtakes)) not in by_src
    assert plan.stats.inside_shoot_skipped == 1
    assert plan.stats.inside_shoot_skipped_bytes == 300


def run_dry(tmp_path, folder, mode, table=SHIPPED_MODES):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},{mode},raw\n", encoding="utf-8")
    return mb.main(["--manifest", str(manifest), "--modes", str(table),
                    "--dest", str(tmp_path / "out"), "--dry-run"])


def test_mode_without_plus_lists_it_as_a_folder_without_export(tmp_path, capsys,
                                                               photobackup_log_dir):
    folder = party_tree(tmp_path / "G")
    assert not party_photos(folder) & planned_set(plan_rows([(folder, "3", "raw")], tmp_path))
    assert run_dry(tmp_path, folder, "3") == 0
    out = capsys.readouterr().out
    unexported = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir,
                                                            "_unexported.csv"))))
    names = {Path(row["folder"]).name: row for row in unexported}
    assert names["Another event"]["files"] == "2"
    assert names["Another event"]["copied"] == "no"
    assert names["more"]["files"] == "1"
    inside = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_inside.csv"))))
    assert [row["subfolder"] for row in inside] == ["_OUTTAKES"]
    assert "inside sessions (subfolders the mode leaves out): 1 /" in out
    assert "folders without export: 2 folders" in out


def test_analyze_and_dry_run_agree_on_it(tmp_path, photobackup_log_dir):
    folder = party_tree(tmp_path / "G")
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},3+,raw\n", encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(SHIPPED_MODES),
                    "--analyze"]) == 0
    rows = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_analysis.csv"))))
    chosen = [row for row in rows if row["chosen"] == "yes"][0]
    assert run_dry(tmp_path, folder, "3+") == 0
    planned = list(csv.DictReader(io.StringIO(latest_csv(photobackup_log_dir, "_plan.csv"))))
    assert sum(int(row["size"]) for row in planned) == int(chosen["bytes"])
    assert int(chosen["bytes"]) == 11 + 10 + 5000 + 5001 + 70


# ---- guard: the outtakes stay where they were ---------------------------------------
def outtakes_tree(root):
    make_tree(root, {
        "2025/shoot/_DSC1.NEF": 10,
        "2025/shoot/_EXPORT/_DSC1_v1.jxl": 11,
        "2025/shoot/_OUTTAKES/DSC_0002.NEF": 12,
        "2025/shoot/_OUTTAKES/deep/DSC_0003.NEF": 13,
        "2025/shoot/_OUTTAKES_DROP/DSC_0004.NEF": 14,
        "2025/shoot/_OUTTAKES_DROP/deep/DSC_0005.NEF": 15,
        "2025/shoot/_OUTTAKES_KEEP/DSC_0006.NEF": 16,
        "2025/shoot/_OUTTAKES_KEEP/deep/DSC_0007.NEF": 17,
    })
    return root / "2025"


OUTTAKES = ("_OUTTAKES/DSC_0002.NEF", "_OUTTAKES/deep/DSC_0003.NEF")
DROP = ("_OUTTAKES_DROP/DSC_0004.NEF", "_OUTTAKES_DROP/deep/DSC_0005.NEF")
KEEP = ("_OUTTAKES_KEEP/DSC_0006.NEF", "_OUTTAKES_KEEP/deep/DSC_0007.NEF")
# mode -> (added, inside sessions)
OUTTAKES_BY_MODE = {
    "1": ((), OUTTAKES + DROP + KEEP), "1+": ((), OUTTAKES + DROP + KEEP),
    "2": ((), OUTTAKES + DROP + KEEP), "2+": ((), OUTTAKES + DROP + KEEP),
    "3": (KEEP, OUTTAKES + DROP), "3+": (KEEP, OUTTAKES + DROP),
    "4": (KEEP + OUTTAKES, DROP), "4+": (KEEP + OUTTAKES, DROP),
    "5": (KEEP + OUTTAKES + DROP, ()), "5+": (KEEP + OUTTAKES + DROP, ()),
}


@pytest.mark.parametrize("mode", sorted(OUTTAKES_BY_MODE))
def test_guard_the_outtakes_stay_inside_sessions_or_added(tmp_path, mode):
    folder = outtakes_tree(tmp_path / "G")
    plan = plan_rows([(folder, mode, "raw")], tmp_path)
    by_src = {os.path.normcase(item.src): item.category for item in plan.files}
    added, inside = OUTTAKES_BY_MODE[mode]
    shoot = folder / "shoot"
    for rel in added:
        assert by_src.get(os.path.normcase(str(shoot / rel))) == sb.CAT_ADDED, rel
    for rel in inside:
        assert os.path.normcase(str(shoot / rel)) not in by_src, rel
    assert plan.stats.inside_shoot_skipped == len(inside)
    assert {row[1] for row in plan.inside} == {rel.split("/")[0] for rel in inside}
    assert not plan.unexported_folders


# ---- a table without LeaveOut, or with *, plans what v0.12.1 planned ---------------
LETTERS = {sb.CAT_EXPORT: "x", sb.CAT_SOURCE: "s", sb.CAT_UNSELECTED: "u",
           sb.CAT_CAPTURE_ONE: "c", sb.CAT_NX_STUDIO: "n", sb.CAT_UNEXPORTED: "e",
           sb.CAT_ADDED: "a", sb.CAT_WHOLE: "w"}


def signature(plan, folder):
    """One letter per listed file under `folder` (sorted): its category, or '.'
    when it is not planned; then the files inside sessions, the _inside.csv rows
    and the folders without an export."""
    by_src = {os.path.normcase(item.src): LETTERS[item.category] for item in plan.files}
    letters = "".join(by_src.get(path, ".") for path in sorted(listed(folder)))
    return (f"{letters} {plan.stats.inside_shoot_skipped} {len(plan.inside)} "
            f"{len(plan.unexported_folders)}")


# What v0.12.1 planned on full_tree (EXCLUDE_FOLDERS = Skipme), taken from its
# code (git show v0.12.1:) with V0121_MODES: see the v0.13.0 report.
V0121_PLANS = {
    ('1', 'raw'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...x.a.sss..x..xc.s................c...n.a...........xs. 31 11 6',
    ('1', 'raw+jpeg'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...x.a.sss..x..xc.s................c...n.a...........xs. 31 11 6',
    ('1', 'all'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...x.a.sss..x..xc.s................c...n.a...........xs. 31 11 6',
    ('2', 'raw'):
        '.ux...u.usu.uuuuu.uuuusx.xs.....x......s....xs..x........x.su..x.a.sss.ux..xc.s.u..............c..un.au..........xs. 31 11 6',
    ('2', 'raw+jpeg'):
        '.ux...u.usu.uuuuu.uuuusx.xs.....x......s....xs..x........x.su..x.a.sssuux..xc.suu..............c..un.au..........xs. 31 11 6',
    ('2', 'all'):
        '.ux...uuusuuuuuuuuuuuusx.xs.....x......s....xs..x........x.su..x.a.sssuux..xcusuu..............c..unuau..........xs. 31 11 6',
    ('3', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusx.xs...aaxaaa..as..aaxs..x........x.su..xaa.sss.ux..xc.s.u.....a.aaa..a.c..un.au..........xs. 16 8 5',
    ('3', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusx.xs...aaxaaa..as.aaaxs..x........x.su..xaa.sssuux..xc.suu....aa.aaa.aa.c..un.au..........xs. 16 8 5',
    ('3', 'all'):
        '.uxaaauuusuuuuuuuuuuuusx.xs...aaxaaaa.asaaaaxs..x........x.su..xaa.sssuux..xcusuu...aaaaaaa.aa.c..unuau..........xs. 16 8 5',
    ('4', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as..aaxs..x........xasu..xaa.sss.ux..xc.s.ua....a.aaa..a.c..un.au..........xs. 13 5 5',
    ('4', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as.aaaxs..x........xasu..xaa.sssuux..xc.suua...aa.aaa.aa.c..un.au..........xs. 13 5 5',
    ('4', 'all'):
        '.uxaaauuusuuuuuuuuuuuusxaxsa..aaxaaaa.asaaaaxs..x........xasu..xaa.sssuux..xcusuua..aaaaaaa.aa.c..unuau..........xs. 13 5 5',
    ('5', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as..aaxs..x........xasu..xaa.sss.ux..xc.s.uaaa..a.aaa..a.c..un.au..........xs. 11 4 5',
    ('5', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as.aaaxs..x........xasu..xaa.sssuux..xc.suuaaa.aa.aaa.aa.c..un.au..........xs. 11 4 5',
    ('5', 'all'):
        '.uxaaauuusuuuuuuuuuuuusxaxsa..aaxaaaa.asaaaaxs..x........xasu..xaa.sssuux..xcusuuaaaaaaaaaa.aa.c..unuau..........xs. 11 4 5',
    ('6', 'raw'):
        '.wwwwwwwwwwwwwwwwwwwwwwwwwww..wwwwwww.wwwwwwww..www.wwwwwwwwww.www.wwwwwwwwwwwwwwwwwwwwwwww.ww.wwwwwwwww.wwww.wwwwww 0 0 0',
    ('6', 'raw+jpeg'):
        '.wwwwwwwwwwwwwwwwwwwwwwwwwww..wwwwwww.wwwwwwww..www.wwwwwwwwww.www.wwwwwwwwwwwwwwwwwwwwwwww.ww.wwwwwwwww.wwww.wwwwww 0 0 0',
    ('6', 'all'):
        '.wwwwwwwwwwwwwwwwwwwwwwwwwww..wwwwwww.wwwwwwww..www.wwwwwwwwww.www.wwwwwwwwwwwwwwwwwwwwwwww.ww.wwwwwwwww.wwww.wwwwww 0 0 0',
    ('1+', 'raw'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...xea.sss..x..xc.s................c...n.a.e.ceee.ecnxse 31 11 6',
    ('1+', 'raw+jpeg'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...xea.sss..x..xc.s................c...n.a.e.ceee.ecnxse 31 11 6',
    ('1+', 'all'):
        '..x......s............sx.xs.....x......s....xs..x........x.s...xea.sss..x..xc.s................c...n.a.e.ceee.ecnxse 31 11 6',
    ('2+', 'raw'):
        '.ux...u.usu.uuuuu.uuuusx.xs.....x......s....xs..x........x.su..xea.sss.ux..xc.s.u..............c..un.aue.ceee.ecnxse 31 11 6',
    ('2+', 'raw+jpeg'):
        '.ux...u.usu.uuuuu.uuuusx.xs.....x......s....xs..x........x.su..xea.sssuux..xc.suu..............c..un.aue.ceee.ecnxse 31 11 6',
    ('2+', 'all'):
        '.ux...uuusuuuuuuuuuuuusx.xs.....x......s....xs..x........x.su..xea.sssuux..xcusuu..............c..unuaue.ceee.ecnxse 31 11 6',
    ('3+', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusx.xs...aaxaaa..as..aaxs..x........x.su..xea.sss.ux..xc.s.u.....a.aaa..a.c..un.aue.ceee.ecnxse 16 8 6',
    ('3+', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusx.xs...aaxaaa..as.aaaxs..x........x.su..xea.sssuux..xc.suu....aa.aaa.aa.c..un.aue.ceee.ecnxse 16 8 6',
    ('3+', 'all'):
        '.uxaaauuusuuuuuuuuuuuusx.xs...aaxaaaa.asaaaaxs..x........x.su..xea.sssuux..xcusuu...aaaaaaa.aa.c..unuaue.ceee.ecnxse 16 8 6',
    ('4+', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as..aaxs..x........xasu..xea.sss.ux..xc.s.ua....a.aaa..a.c..un.aue.ceee.ecnxse 13 5 6',
    ('4+', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as.aaaxs..x........xasu..xea.sssuux..xc.suua...aa.aaa.aa.c..un.aue.ceee.ecnxse 13 5 6',
    ('4+', 'all'):
        '.uxaaauuusuuuuuuuuuuuusxaxsa..aaxaaaa.asaaaaxs..x........xasu..xea.sssuux..xcusuua..aaaaaaa.aa.c..unuaue.ceee.ecnxse 13 5 6',
    ('5+', 'raw'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as..aaxs..x........xasu..xea.sss.ux..xc.s.uaaa..a.aaa..a.c..un.aue.ceee.ecnxse 11 4 6',
    ('5+', 'raw+jpeg'):
        '.uxa.au.usu.uuuuu.uuuusxaxsa..aaxaaa..as.aaaxs..x........xasu..xea.sssuux..xc.suuaaa.aa.aaa.aa.c..un.aue.ceee.ecnxse 11 4 6',
    ('5+', 'all'):
        '.uxaaauuusuuuuuuuuuuuusxaxsa..aaxaaaa.asaaaaxs..x........xasu..xea.sssuux..xcusuuaaaaaaaaaa.aa.c..unuaue.ceee.ecnxse 11 4 6',
    ('raw-only', 'raw'):
        '...w.....ww..w..w....ww...ww..ww.w.w...w..w..w...w..w.....wwww......ww.w....w.w.w.ww..w......w.www.w...w.w..w.www.w. 0 0 0',
    ('raw-only', 'raw+jpeg'):
        '...w.....ww..w..w....ww...ww..ww.w.w...w..w..w...w..w.....wwww......ww.w....w.w.w.ww..w......w.www.w...w.w..w.www.w. 0 0 0',
    ('raw-only', 'all'):
        '...w.....ww..w..w....ww...ww..ww.w.w...w..w..w...w..w.....wwww......ww.w....w.w.w.ww..w......w.www.w...w.w..w.www.w. 0 0 0',
    ('jpeg-only', 'raw'):
        '........................w.........w...w..w.w....w.w...www..........w..w...w....w.....w....w.w.....w........w........ 0 0 0',
    ('jpeg-only', 'raw+jpeg'):
        '........................w.........w...w..w.w....w.w...www..........w..w...w....w.....w....w.w.....w........w........ 0 0 0',
    ('jpeg-only', 'all'):
        '........................w.........w...w..w.w....w.w...www..........w..w...w....w.....w....w.w.....w........w........ 0 0 0',
    ('jxl-only', 'raw'):
        '..w....................w.w......w...........w............w.....w........w..w.....................................w.. 0 0 0',
    ('jxl-only', 'raw+jpeg'):
        '..w....................w.w......w...........w............w.....w........w..w.....................................w.. 0 0 0',
    ('jxl-only', 'all'):
        '..w....................w.w......w...........w............w.....w........w..w.....................................w.. 0 0 0',
    ('skip', 'raw'):
        '.................................................................................................................... 0 0 0',
    ('skip', 'raw+jpeg'):
        '.................................................................................................................... 0 0 0',
    ('skip', 'all'):
        '.................................................................................................................... 0 0 0',
}


@pytest.mark.parametrize("table", ["without the column", "with *"])
@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
@pytest.mark.parametrize("mode", ALL_MODES + ("raw-only", "jpeg-only", "jxl-only", "skip"))
def test_guard_an_old_table_plans_what_v0121_planned(tmp_path, monkeypatch, mode, originals,
                                                     table):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    path = v0121_table(tmp_path) if table == "without the column" else star_table(tmp_path)
    plan = plan_rows([(folder, mode, originals)], tmp_path, path)
    assert signature(plan, folder) == V0121_PLANS[(mode, originals)]


def test_a_table_without_the_column_reads_as_star(tmp_path):
    modes = mb.read_modes(str(v0121_table(tmp_path)))
    assert modes["3+"].leave_out == ("*",)
    assert mb._row_rules(modes["3+"], "raw").leave_out == ("*",)
    assert sb.FolderRules().leave_out == ("*",)


def test_the_shipped_table_names_the_outtakes(tmp_path):
    modes = mb.read_modes(str(SHIPPED_MODES))
    for name in ALL_MODES:
        expected = () if modes[name].whole else ("_OUTTAKES*",)
        assert modes[name].leave_out == expected, name
    for name in ("raw-only", "jpeg-only", "jxl-only", "skip"):
        assert modes[name].leave_out == (), name


# ---- an empty LeaveOut leaves nothing out ---------------------------------------------
def test_an_empty_leave_out_copies_the_outtakes_as_a_folder_without_export(tmp_path):
    folder = outtakes_tree(tmp_path / "G")
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,LeaveOut,"
                     "Description\nX+,yes,,,,no,yes,,t\nX,yes,,,,no,no,,t\n", encoding="utf-8")
    plan = plan_rows([(folder, "X+", "raw")], tmp_path, table)
    by_src = {os.path.normcase(item.src): item.category for item in plan.files}
    shoot = folder / "shoot"
    for rel in OUTTAKES + DROP + KEEP:
        assert by_src.get(os.path.normcase(str(shoot / rel))) == sb.CAT_UNEXPORTED, rel
    assert plan.stats.inside_shoot_skipped == 0 and not plan.inside
    plan = plan_rows([(folder, "X", "raw")], tmp_path, table)
    assert not any("_OUTTAKES" in item.src for item in plan.files)
    assert plan.stats.inside_shoot_skipped == 0
    assert len(plan.unexported_folders) == 6


def test_a_name_added_to_leave_out_is_left_out(tmp_path):
    folder = party_tree(tmp_path / "G")
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,LeaveOut,"
                     "Description\nX+,yes,,,,no,yes,_OUTTAKES*;another *,t\n", encoding="utf-8")
    plan = plan_rows([(folder, "X+", "raw")], tmp_path, table)
    assert not party_photos(folder) & planned_set(plan)
    assert {row[1] for row in plan.inside} == {"Another event", "_OUTTAKES"}
    assert plan.stats.inside_shoot_skipped == 4


# ---- LeaveOut with Whole or Skip aborts ----------------------------------------------
@pytest.mark.parametrize("line, words", [
    ("X,no,,,,yes,no,,_OUTTAKES*,t", "Whole=yes"),
    ("X,no,,,,no,no,yes,_OUTTAKES*,t", "Skip=yes"),
])
def test_leave_out_with_whole_or_skip_aborts(tmp_path, capsys, line, words):
    table = tmp_path / "modes.csv"
    table.write_text("Mode,Unselected,AddFolders,AddWhole,Exclude,Whole,Unexported,Skip,"
                     "LeaveOut,Description\n" + line + "\n", encoding="utf-8")
    folder = party_tree(tmp_path / "G")
    dest = tmp_path / "out"
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(f"Folder,Mode,Originals\n{folder},X,raw\n", encoding="utf-8")
    assert mb.main(["--manifest", str(manifest), "--modes", str(table),
                    "--dest", str(dest), "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert words in err and "LeaveOut" in err
    assert not dest.exists()


# ---- the invariants with the new table, on the invariant tree ---------------------
@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
def test_a_bigger_mode_never_copies_less_on_the_invariant_tree(tmp_path, monkeypatch,
                                                                originals):
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plans = {name: planned_set(plan_rows([(folder, name, originals)], tmp_path))
             for name in ALL_MODES}
    for small, big in CHAINS:
        missing = sorted(plans[small] - plans[big])
        assert not missing, f"mode {small} copies what mode {big} does not: {missing}"


@pytest.mark.parametrize("originals", ["raw", "raw+jpeg", "all"])
@pytest.mark.parametrize("mode", ALL_MODES)
def test_never_less_than_v0121(tmp_path, monkeypatch, mode, originals):
    """Every file v0.12.1 planned (the fixed list) is still planned."""
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    plan = plan_rows([(folder, mode, originals)], tmp_path)
    paths = sorted(listed(folder))
    old = V0121_PLANS[(mode, originals)].split()[0]
    new = signature(plan, folder).split()[0]
    lost = [path for path, before, now in zip(paths, old, new) if before != "." and now == "."]
    assert not lost, lost


def test_the_new_folders_are_copied_in_a_plus_mode(tmp_path, monkeypatch):
    """On the invariant tree, 3+ now copies the session subfolders it does not name."""
    monkeypatch.setattr(sb, "EXCLUDE_FOLDERS", "Skipme")
    folder = full_tree(tmp_path / "G")
    found = {os.path.normcase(item.src): item.category
             for item in plan_rows([(folder, "3+", "raw")], tmp_path).files}
    shoot = folder / "shoot"
    for rel in ("Other event/IMG_0101.JPG", "Other event/clip.mp4",
                "Other event/CaptureOne/Settings166/IMG_0101.JPG.cos",
                "Other event/deeper/IMG_0103.JPG", "Other event/_OUTTAKES/IMG_0104.JPG",
                "Selected/rejects/DSC_0073.NEF", "Edited/cam2/DSC_0080.NEF"):
        assert os.path.normcase(str(shoot / rel)) in found, rel
    assert os.path.normcase(str(shoot / "Other event/CaptureOne/Cache/Proxies/IMG_0101.cop")) \
        not in found
    for rel in ("Selected/_OUTTAKES/DSC_0072.NEF", "_OUTTAKES/Picked/DSC_0062.NEF"):
        assert os.path.normcase(str(shoot / rel)) not in found, rel
    # inside _OUTTAKES_KEEP (an AddFolders folder) the Originals filter still decides
    assert found[os.path.normcase(str(shoot / "_OUTTAKES_KEEP/Picked/DSC_0090.NEF"))] \
        == sb.CAT_ADDED
    assert os.path.normcase(str(shoot / "_OUTTAKES_KEEP/Picked/clip.mov")) not in found
