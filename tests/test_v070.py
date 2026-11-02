"""v0.7.0 regression tests — import cycle, list segmentation, CLI honesty.

All run keyless / offline so CI stays green.

* **fix-public-module-import-cycle** — ``import duanhui.placement`` (or
  ``duanhui.style``) as the first import of a fresh interpreter raised
  ``ImportError: cannot import name '…' from partially initialized module …``.
  The suite masked it because pytest's first test imports ``duanhui.cli`` and
  warms the module graph, so these tests use a subprocess per module.
* **fix-list-block-paragraph-merge** — consecutive list items in one blank-line
  block were fused into a single ``Segment.text`` with no separator.
* **fix-cover-export-summary** — the cover run reported ``images/ 1 张 PNG``
  while the bundle holds ``cover.png`` at its root.
* **fix-out-dir-traceback** — ``-o <existing file>`` escaped as a raw
  NotADirectoryError / FileExistsError traceback instead of a clean exit 2.
* **fix-version-and-doc-surface-lockstep** — the two stale CLI help strings
  (the derived version/surface guard lives in ``tests/test_version_lockstep.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from duanhui.cli import app
from duanhui.segment import segment_article

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "examples" / "sample_article.md"


def _python_first_import(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter rooted at the repo (no warm imports)."""
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# fix-public-module-import-cycle
# --------------------------------------------------------------------------- #


def test_pipeline_modules_import_as_first_import() -> None:
    # v0.6.0 bug repro: exit 1 with "partially initialized module" ImportError.
    for module in ("duanhui.placement", "duanhui.style"):
        proc = _python_first_import(f"import {module}")
        assert proc.returncode == 0, proc.stderr


def test_documented_entry_points_import_as_first_import() -> None:
    proc = _python_first_import(
        "from duanhui.placement import PlacementPlan, Spot, plan_article\n"
        "from duanhui.style import StyleLock, build_styled_spots, load_style_pack"
    )
    assert proc.returncode == 0, proc.stderr


# --------------------------------------------------------------------------- #
# fix-list-block-paragraph-merge
# --------------------------------------------------------------------------- #


def test_consecutive_list_items_become_separate_segments() -> None:
    # v0.6.0 bug repro: one segment
    # "第一个要点…第二个要点…第三个要点…" (no separator).
    art = (
        "# 标题\n\n这是引言段落，讲的是核心概念。\n\n"
        "1. 第一个要点，讲的是某机制\n"
        "2. 第二个要点，举例如下\n"
        "3. 第三个要点，最后总结\n"
    )
    texts = [s.text for s in segment_article(art)]
    assert "第一个要点，讲的是某机制" in texts, texts
    assert "第二个要点，举例如下" in texts, texts
    assert "第三个要点，最后总结" in texts, texts
    assert not any(
        "第一个要点，讲的是某机制第二个要点" in t for t in texts
    ), f"list items were fused: {texts}"


def test_english_list_items_become_separate_segments() -> None:
    art = "Intro paragraph here for context.\n\n1. First point\n2. Second point\n"
    assert [s.text for s in segment_article(art)] == [
        "Intro paragraph here for context.",
        "First point",
        "Second point",
    ]


def test_list_continuation_line_stays_with_its_item() -> None:
    art = (
        "# 标题\n\n这是引言段落，讲的是核心概念。\n\n"
        "1. 第一个要点很长\n"
        "   续行仍然属于第一条\n"
        "2. 第二个要点\n"
    )
    texts = [s.text for s in segment_article(art)]
    assert "第一个要点很长续行仍然属于第一条" in texts, texts
    assert "第二个要点" in texts, texts


def test_decimal_paragraph_is_not_split_as_a_list_item() -> None:
    # regression guard: "1.5倍" is text, not an ordered-list marker "1.".
    art = "# 标题\n\n这是引言。\n\n1.5倍的增长来自这个核心机制。\n"
    assert any(t.startswith("1.5倍的增长") for t in (s.text for s in segment_article(art)))


# --------------------------------------------------------------------------- #
# fix-cover-export-summary
# --------------------------------------------------------------------------- #


def test_cover_run_reports_cover_png_at_bundle_root(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "--cover", "-o", str(out)])
    assert result.exit_code == 0, result.stdout
    assert (out / "cover.png").is_file()
    assert not list((out / "images").glob("*.png"))
    assert "cover.png" in result.stdout
    assert "贴进文章封面位置" in result.stdout
    assert "images/ 里的 PNG" not in result.stdout


def test_batch_run_still_reports_the_images_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "-n", "1", "-o", str(out)])
    assert result.exit_code == 0, result.stdout
    assert (out / "images" / "illustration_01.png").is_file()
    assert "images/" in result.stdout
    assert "把 images/ 里的 PNG" in result.stdout


# --------------------------------------------------------------------------- #
# fix-out-dir-traceback
# --------------------------------------------------------------------------- #


def test_out_dir_pointing_at_a_file_exits_cleanly(tmp_path, monkeypatch) -> None:
    # v0.6.0 bug repro: rc=1 + a rich NotADirectoryError traceback.
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    blocker = tmp_path / "not-a-dir.md"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["run", str(SAMPLE), "-n", "1", "-o", str(blocker)])
    assert result.exit_code == 2, result.stdout
    assert "无法创建导出目录" in result.stdout


def test_cover_out_dir_pointing_at_a_file_exits_cleanly(tmp_path, monkeypatch) -> None:
    # v0.6.0 bug repro: rc=1 + a rich FileExistsError traceback.
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    blocker = tmp_path / "not-a-dir.md"
    blocker.write_text("x", encoding="utf-8")
    result = runner.invoke(app, ["run", str(SAMPLE), "--cover", "-o", str(blocker)])
    assert result.exit_code == 2, result.stdout
    assert "无法创建导出目录" in result.stdout


# --------------------------------------------------------------------------- #
# fix-version-and-doc-surface-lockstep (stale user-visible CLI text)
# --------------------------------------------------------------------------- #


def test_list_styles_help_has_no_stale_release_version() -> None:
    result = runner.invoke(app, ["list-styles", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "v0.2.0" not in result.stdout


def test_run_help_does_not_claim_a_fixed_six_to_eight_default() -> None:
    # the real default is auto-estimated and clamped to [1, 8]
    # (default_max_spots(4) == 2), so the old "6–8 张" claim was wrong.
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0, result.stdout
    assert "6–8" not in result.stdout
