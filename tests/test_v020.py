"""v0.2.0 regression tests — m4 (image-backend fix), m5 (cover mode), m6 (style selection).

These extend the v0.1.0 keyless suite. All run on mock backends / stdlib only —
no API key, no network — so CI stays green and the dry-run is reproducible.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from duanhui.backends import make_image_backend, make_llm_backend
from duanhui.backends.image import MockImageBackend
from duanhui.cli import app
from duanhui.config import Config, is_valid_backend, is_valid_image_backend
from duanhui.cover import (
    COVER_FILENAME,
    build_cover_styled_spot,
    export_cover,
    plan_cover,
    render_cover,
)
from duanhui.style import (
    DEFAULT_PACK_ID,
    StyleLock,
    available_style_packs,
    build_styled_spots,
    load_style_pack,
)
from duanhui.placement import plan_article
from duanhui.segment import segment_article

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "examples" / "sample_article.md"

runner = CliRunner()


@pytest.fixture(scope="module")
def sample_text() -> str:
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def segments(sample_text: str) -> list:
    return segment_article(sample_text)


# --------------------------------------------------------------------------- #
# m4 — unknown image-backend name degrades to mock (symmetry with LLM side)
# --------------------------------------------------------------------------- #


def test_is_valid_image_backend_exists() -> None:
    assert is_valid_image_backend("mock")
    assert is_valid_image_backend("tongyi-wanxiang")
    assert is_valid_image_backend("kling")
    assert not is_valid_image_backend("totallybogus-xyz")
    assert not is_valid_image_backend("")


def test_unknown_image_backend_with_key_degrades_to_mock() -> None:
    # v0.1.0 bug repro: a typo'd image_backend name + a key present used to make
    # effective_image_backend() return the bogus name and make_image_backend()
    # raise KeyError at render. v0.2.0 degrades to mock instead.
    cfg = Config(image_backend="totallybogus-xyz", image_api_key="sk-test")
    assert cfg.effective_image_backend() == "mock"
    assert cfg.uses_mock_image() is True
    backend = make_image_backend(cfg.effective_image_backend(), api_key=cfg.image_api_key)
    assert isinstance(backend, MockImageBackend)  # no crash


def test_unknown_llm_backend_via_direct_construction_degrades_to_mock() -> None:
    # The v0.1.0 validity guard lived only in Config.load(); a directly-built
    # Config was unprotected. v0.2.0 makes effective_llm_backend() validate too.
    cfg = Config(llm_backend="totallybogus-xyz", llm_api_key="sk-test")
    assert cfg.effective_llm_backend() == "mock"
    assert cfg.uses_mock_llm() is True


def test_real_backends_still_resolve_when_keyed() -> None:
    # regression guard: the happy path must not be regressed by the validity check.
    assert Config(image_backend="tongyi-wanxiang", image_api_key="sk").effective_image_backend() == "tongyi-wanxiang"
    assert Config(llm_backend="deepseek", llm_api_key="sk").effective_llm_backend() == "deepseek"
    assert is_valid_backend("deepseek") and is_valid_backend("mock")


def test_config_load_unknown_image_backend_degrades_to_mock(tmp_path, monkeypatch) -> None:
    for v in ("DASHSCOPE_API_KEY", "DUANHUI_MOCK"):
        monkeypatch.delenv(v, raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("image_backend: totallybogus-xyz\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.uses_mock_image()  # no key + unknown name -> mock


# --------------------------------------------------------------------------- #
# m5 — cover mode
# --------------------------------------------------------------------------- #


def test_plan_cover_derives_brief_from_title(segments) -> None:
    plan = plan_cover(segments, article_title="为什么你的待办清单总是越列越长？")
    assert plan.depicts.strip()
    assert "待办清单" in plan.depicts  # distilled from the title
    assert plan.article_title == "为什么你的待办清单总是越列越长？"
    assert plan.reason  # non-empty rationale


def test_plan_cover_falls_back_to_intro_when_no_title(segments) -> None:
    plan = plan_cover(segments, article_title="")
    assert plan.depicts.strip()
    # the intro paragraph mentions the article theme
    assert len(plan.depicts) <= 24


def test_plan_cover_empty_segments_raises() -> None:
    with pytest.raises(ValueError):
        plan_cover([], article_title="x")


def test_build_cover_styled_spot_shares_pack_seed(segments, style_shuimo: StyleLock) -> None:
    plan = plan_cover(segments, article_title="待办清单")
    spot = build_cover_styled_spot(plan, style_shuimo)
    assert spot.seed == style_shuimo.consistency_seed  # the consistency lock
    assert spot.pack_id == style_shuimo.pack_id
    assert spot.depicts in spot.prompt  # brief fused into the locked preamble


def test_render_and_export_cover_produces_bundle(
    segments, style_shuimo: StyleLock, tmp_path
) -> None:
    plan = plan_cover(segments, article_title="待办清单")
    styled = build_cover_styled_spot(plan, style_shuimo)
    rendered = render_cover(styled, MockImageBackend(), tmp_path / "out")
    assert rendered.path.name == COVER_FILENAME
    assert rendered.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"  # valid PNG

    bundle = export_cover(
        styled, rendered, style_shuimo, tmp_path / "out", article_title="待办清单"
    )
    assert bundle.image_count == 1
    assert bundle.placement_map_path.exists()
    assert bundle.annotated_md_path.exists()
    assert COVER_FILENAME in {p.name for p in bundle.image_paths}

    data = json.loads(bundle.placement_map_path.read_text(encoding="utf-8"))
    assert data["image_count"] == 1
    assert data["entries"][0]["file"] == COVER_FILENAME
    assert data["entries"][0]["seed"] == style_shuimo.consistency_seed
    assert data["pack_id"] == "shuimo"
    md = bundle.annotated_md_path.read_text(encoding="utf-8")
    assert COVER_FILENAME in md


def test_cli_cover_dry_run_prints_plan_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "--cover", "--dry-run", "-o", str(out_dir)])
    assert result.exit_code == 0, result.stdout
    assert "封面计划" in result.stdout
    assert not out_dir.exists()


def test_cli_cover_full_run_produces_cover_png(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "--cover", "-o", str(out_dir)])
    assert result.exit_code == 0, result.stdout
    cover = out_dir / COVER_FILENAME
    assert cover.exists()
    assert cover.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (out_dir / "placement_map.json").exists()
    assert (out_dir / "annotated.md").exists()
    # cover mode writes no body illustrations
    assert not list((out_dir / "images").glob("illustration_*.png"))


# --------------------------------------------------------------------------- #
# m6 — runtime style pack selection
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def style_guaidan() -> StyleLock:
    return load_style_pack(pack_id="guaidan")


@pytest.fixture(scope="module")
def style_shuimo() -> StyleLock:
    return load_style_pack(pack_id="shuimo")


def test_available_style_packs_lists_both() -> None:
    packs = available_style_packs()
    assert packs[0] == DEFAULT_PACK_ID  # guaidan first
    assert "shuimo" in packs
    assert len(packs) >= 2


def test_shuimo_pack_loads_with_distinct_seed(style_guaidan: StyleLock, style_shuimo: StyleLock) -> None:
    assert style_shuimo.pack_id == "shuimo"
    assert style_shuimo.consistency_seed != style_guaidan.consistency_seed  # distinct hands
    assert style_shuimo.aspect_wh() == (16, 9)
    assert "{{ depicts }}" in style_shuimo.style_preamble
    assert style_shuimo.consistency_seed == 41731


def test_shuimo_render_prompt_differs_from_guaidan(
    style_guaidan: StyleLock, style_shuimo: StyleLock
) -> None:
    brief = "一个塞满便签的背包"
    pg = style_guaidan.render_prompt(brief)
    ps = style_shuimo.render_prompt(brief)
    assert brief in pg and brief in ps
    assert pg != ps  # different locked preambles
    assert "水墨" in ps or "毛笔" in ps
    assert "{{" not in pg and "{{" not in ps


def test_load_unknown_pack_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_style_pack(pack_id="does-not-exist")


def test_cli_list_styles_lists_both(monkeypatch) -> None:
    result = runner.invoke(app, ["list-styles"])
    assert result.exit_code == 0, result.stdout
    assert "guaidan" in result.stdout
    assert "shuimo" in result.stdout


def test_cli_run_with_style_shuimo_uses_shuimo_pack(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "--style", "shuimo", "-o", str(out_dir)])
    assert result.exit_code == 0, result.stdout
    assert "shuimo" in result.stdout  # the chosen pack is reported
    data = json.loads((out_dir / "placement_map.json").read_text(encoding="utf-8"))
    assert data["pack_id"] == "shuimo"
    assert data["consistency_seed"] == 41731
    pngs = list((out_dir / "images").glob("illustration_*.png"))
    assert pngs
    for p in pngs:
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_cli_run_with_unknown_style_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    result = runner.invoke(app, ["run", str(SAMPLE), "--style", "nope", "-o", str(tmp_path / "o")])
    assert result.exit_code == 2
    assert "找不到风格包" in result.stdout


def test_cli_default_style_still_guaidan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "-o", str(out_dir)])
    assert result.exit_code == 0, result.stdout
    data = json.loads((out_dir / "placement_map.json").read_text(encoding="utf-8"))
    assert data["pack_id"] == DEFAULT_PACK_ID  # default unchanged
    assert data["consistency_seed"] == 73219
