"""v0.6.0 regression tests — CN-input fidelity fixes + version bump.

All run keyless / offline so CI stays green and the fixes are reproducible
without a real API key.

* **fix-article-decode-gbk-traceback** — a GBK/GB18030-encoded article (the
  default Windows-Notepad encoding for Chinese authors) raised an uncaught
  ``UnicodeDecodeError`` traceback out of ``_read_article`` (its guard only
  caught OSError). ``_read_article`` now retries with the gb18030 superset
  codec and degrades to a clean exit-2 message when the file is neither
  UTF-8 nor GB18030.
* **fix-segment-markdown-cleanup-gaps** — blockquote ``>`` markers and
  spaceless CJK ordered-list markers (``1.要点`` / ``1、要点``) leaked into
  ``Segment.text``, and a second heading inside one blank-line block merged
  into its body instead of becoming its own paragraph.
* **fix-cjk-hardwrap-join-space** — hard-wrapped CJK lines got a spurious
  half-width space at every wrap boundary from the ``" ".join`` re-joining.
* **single-source-of-truth version test** — VERSION file == ``__version__``
  == CLI version == ``web/site.json`` content_version == CHANGELOG head.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from duanhui import __version__
from duanhui.cli import _read_article, app
from duanhui.segment import segment_article

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# fix-article-decode-gbk-traceback
# --------------------------------------------------------------------------- #


def _gbk_article_bytes() -> bytes:
    return (
        "# 为什么清单越列越长\n\n"
        "这是一段核心概念段落，讲的是决策疲劳的机制。\n\n"
        "比如一份混着回邮件、买牙膏的清单。\n"
    ).encode("gb18030")


def test_gbk_article_decodes_via_read_article(tmp_path) -> None:
    # v0.6.0 bug repro: read_text(encoding="utf-8") on a GBK file raised an
    # uncaught UnicodeDecodeError (a ValueError, NOT the guarded OSError).
    path = tmp_path / "gbk_article.md"
    path.write_bytes(_gbk_article_bytes())
    text = _read_article(path)
    assert "决策疲劳" in text


def test_gbk_article_cli_dry_run_prints_plan(tmp_path) -> None:
    # end-to-end: a GBK article must run the keyless dry-run cleanly instead
    # of dying with a UnicodeDecodeError traceback (v0.5.0 behaviour).
    path = tmp_path / "gbk_article.md"
    path.write_bytes(_gbk_article_bytes())
    result = runner.invoke(app, ["run", str(path), "--dry-run"])
    assert result.exit_code == 0, result.stdout
    assert "分段完成" in result.stdout
    assert "配图计划" in result.stdout


def test_undecodable_file_exits_cleanly(tmp_path) -> None:
    # bytes that are neither valid UTF-8 nor GB18030 must produce the clean
    # red message + exit 2, never a traceback.
    path = tmp_path / "junk.md"
    path.write_bytes(b"\x80\xff\x80\xff")
    result = runner.invoke(app, ["run", str(path), "--dry-run"])
    assert result.exit_code == 2, result.stdout


# --------------------------------------------------------------------------- #
# fix-segment-markdown-cleanup-gaps
# --------------------------------------------------------------------------- #


def test_blockquote_marker_stripped_from_segment_text() -> None:
    art = "# 标题\n\n这是引言。\n\n> 这是一段引用的名言，出自某本书。\n"
    texts = [s.text for s in segment_article(art)]
    assert "> 这是一段引用的名言，出自某本书。" not in texts
    assert "这是一段引用的名言，出自某本书。" in texts


def test_spaceless_cjk_ordered_list_markers_stripped() -> None:
    # Chinese IME input typically writes list markers without a space:
    # "1.要点" / "1、要点" — the marker must not leak into Segment.text.
    art = "# 标题\n\n这是引言。\n\n1.第一个要点，讲的是某机制。\n\n2、第二个要点，举例如下。\n"
    texts = [s.text for s in segment_article(art)]
    assert any(t.startswith("第一个要点") for t in texts), texts
    assert any(t.startswith("第二个要点") for t in texts), texts


def test_numbered_decimal_is_not_corrupted() -> None:
    # "1.5倍" / "3.14" start with digit+dot but are NOT list markers; the
    # marker stripper must leave them intact.
    art = "# 标题\n\n这是引言。\n\n1.5倍的增长来自这个核心机制。\n"
    texts = [s.text for s in segment_article(art)]
    assert any("1.5倍的增长" in t for t in texts), texts


def test_second_heading_in_block_becomes_own_paragraph() -> None:
    # v0.6.0 bug repro: only lines[0] was split out as a heading, so "## 小标题"
    # merged into its body ("小标题 正文内容…"). A heading-only line must always
    # become its own (transition-tagged) paragraph.
    art = "# 大标题\n## 小标题\n正文内容在这里，讲的是核心概念。\n"
    segs = segment_article(art)
    assert len(segs) == 3, [s.text for s in segs]
    assert segs[1].text == "小标题"
    assert segs[1].is_heading
    assert segs[2].text == "正文内容在这里，讲的是核心概念。"
    assert not segs[2].is_heading


# --------------------------------------------------------------------------- #
# fix-cjk-hardwrap-join-space
# --------------------------------------------------------------------------- #


def test_hardwrapped_cjk_lines_join_without_space() -> None:
    art = (
        "这是一段被编辑器硬换行的长段落，第一行在这里结束，\n"
        "第二行从这里继续，中间不该出现多余的空格。\n"
    )
    segs = segment_article(art)
    assert len(segs) == 1
    assert "结束， 第二行" not in segs[0].text
    assert "结束，第二行" in segs[0].text


def test_hardwrapped_english_lines_keep_space() -> None:
    # regression guard: ASCII words hard-wrapped across lines still need the
    # space join, otherwise "some wor" + "ds" would fuse into "swords".
    art = "some wrapped english words\ncontinue on the next line here\n"
    segs = segment_article(art)
    assert segs[0].text == "some wrapped english words continue on the next line here"


# --------------------------------------------------------------------------- #
# single-source-of-truth version test
# --------------------------------------------------------------------------- #


def test_version_surfaces_agree() -> None:
    """VERSION file == __version__ == CLI version output == site
    content_version == CHANGELOG head, so a version bump can never leave one
    surface echoing the old version."""
    expected = "0.6.0"

    # VERSION file
    version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version_file == expected, f"VERSION file is {version_file!r}"

    # __version__
    assert __version__ == expected, f"__version__ is {__version__!r}"

    # CLI version command
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stdout
    assert expected in result.stdout, f"CLI version output: {result.stdout!r}"

    # web/site.json content_version
    site = json.loads(
        (REPO_ROOT / "web" / "site.json").read_text(encoding="utf-8")
    )
    assert site.get("content_version") == expected, (
        f"site.json content_version is {site.get('content_version')!r}"
    )

    # CHANGELOG head (the newest released section header)
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{expected}]" in changelog, "CHANGELOG head missing v0.6.0 entry"
    # the v0.6.0 entry must be above the v0.5.0 entry (newest first).
    assert changelog.index(f"## [{expected}]") < changelog.index("## [0.5.0]")
