"""End-to-end tests for the keyless DuanHui pipeline.

These run entirely on the mock backends — no API key, no network — so CI stays
green and anyone can reproduce the dry-run locally. All three milestones are
covered here:

* **m1** — segment a real-shaped Chinese article, then produce a sensible,
  deterministic placement plan via ``MockLLMBackend``.
* **m2** — style-lock each spot with ``styles/guaidan.yaml`` and render a
  consistent batch of valid PNGs through ``MockImageBackend``.
* **m3** — assemble images + ``placement_map.json`` + ``annotated.md`` into one
  drop-in export folder, and drive the whole thing through the ``duanhui`` CLI.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from typer.testing import CliRunner

from duanhui.backends import (
    available_image_backends,
    available_llm_backends,
    is_mock_image,
    is_mock_llm,
    make_image_backend,
    make_llm_backend,
)
from duanhui.backends.image import MockImageBackend
from duanhui.backends.llm import LLMBackend, MockLLMBackend
from duanhui.cli import app
from duanhui.config import Config
from duanhui.export import PlacementMap, export_bundle
from duanhui.placement import PlacementPlan, Spot, default_max_spots, plan_article
from duanhui.segment import Segment, SegmentRole, segment_article
from duanhui.style import StyleLock, build_styled_spots, load_style_pack

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE = REPO_ROOT / "examples" / "sample_article.md"

runner = CliRunner()


@pytest.fixture(scope="module")
def style() -> StyleLock:
    return load_style_pack()


@pytest.fixture(scope="module")
def sample_text() -> str:
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def segments(sample_text: str) -> list[Segment]:
    return segment_article(sample_text)


# --------------------------------------------------------------------------- #
# segment.py
# --------------------------------------------------------------------------- #

def test_sample_article_exists() -> None:
    assert SAMPLE.exists(), "demo sample article must ship with the repo"


def test_segments_are_contiguous_and_ordered(segments: list[Segment]) -> None:
    assert len(segments) >= 6, "a real article should yield several segments"
    for i, seg in enumerate(segments):
        assert seg.idx == i, "segment idx must equal its position"
        assert seg.text, "no empty segment text"
        assert isinstance(seg.role, SegmentRole)


def test_first_segment_is_intro(segments: list[Segment]) -> None:
    assert segments[0].role is SegmentRole.INTRO


def test_markdown_headings_become_transitions(segments: list[Segment]) -> None:
    # The sample has "## 帕金森定律：工作会自动膨胀" style headings; those must be
    # split out as their own transition segments, never merged into prose.
    heading_segs = [s for s in segments if s.text.startswith("帕金森定律")]
    assert heading_segs, "heading line should appear as its own segment"
    assert heading_segs[0].role is SegmentRole.TRANSITION


def test_concept_and_example_roles_detected(segments: list[Segment]) -> None:
    roles = {s.role for s in segments}
    assert SegmentRole.CONCEPT in roles, "explanatory paragraphs → concept"
    assert SegmentRole.EXAMPLE in roles, "比如/举例 paragraphs → example"


def test_segmentation_is_deterministic(sample_text: str) -> None:
    a = [(s.idx, s.role) for s in segment_article(sample_text)]
    b = [(s.idx, s.role) for s in segment_article(sample_text)]
    assert a == b


def test_empty_article_rejected() -> None:
    with pytest.raises(ValueError):
        segment_article("   \n\n  ")


def test_plain_text_without_markdown_still_segments() -> None:
    text = "这是开头段落，介绍主题。\n\n比如说，这里有一个具体例子来解释概念。"
    segs = segment_article(text)
    assert len(segs) == 2
    assert segs[0].role is SegmentRole.INTRO
    assert segs[1].role is SegmentRole.EXAMPLE


# --------------------------------------------------------------------------- #
# backends / registry
# --------------------------------------------------------------------------- #

def test_mock_backend_needs_no_key() -> None:
    backend = make_llm_backend("mock")
    assert isinstance(backend, MockLLMBackend)
    assert backend.requires_key is False


def test_registry_lists_mock_and_real_backends() -> None:
    names = available_llm_backends()
    assert names[0] == "mock"
    for real in ("deepseek", "qwen", "glm"):
        assert real in names


def test_unknown_backend_raises() -> None:
    with pytest.raises(KeyError):
        make_llm_backend("definitely-not-a-model")


def test_real_backend_without_key_raises() -> None:
    with pytest.raises(ValueError):
        make_llm_backend("deepseek", api_key="")


def test_is_mock_llm_recognises_aliases() -> None:
    for alias in ("mock", "", "none", "off", "MOCK"):
        assert is_mock_llm(alias)
    assert not is_mock_llm("deepseek")


# --------------------------------------------------------------------------- #
# placement.py — the core primitive
# --------------------------------------------------------------------------- #

def test_plan_on_sample_is_sensible(segments: list[Segment]) -> None:
    plan = plan_article(segments, MockLLMBackend(), article_title="待办清单")
    assert isinstance(plan, PlacementPlan)
    assert not plan.is_empty()
    assert 1 <= plan.count <= default_max_spots(len(segments))

    valid_idx = {s.idx for s in segments}
    transition_idx = {s.idx for s in segments if s.role is SegmentRole.TRANSITION}
    for spot in plan.spots:
        assert isinstance(spot, Spot)
        assert spot.after_segment_idx in valid_idx
        assert spot.depicts.strip()
        # the mock never illustrates a pure transition paragraph.
        assert spot.after_segment_idx not in transition_idx


def test_plan_spots_are_ordered_and_unique(segments: list[Segment]) -> None:
    plan = plan_article(segments, MockLLMBackend())
    idxs = plan.segment_indices()
    assert idxs == sorted(idxs), "spots must be in document order"
    assert len(idxs) == len(set(idxs)), "no duplicate segment gets two spots"


def test_plan_respects_max_spots(segments: list[Segment]) -> None:
    plan = plan_article(segments, MockLLMBackend(), max_spots=3)
    assert plan.count <= 3


def test_plan_is_deterministic(segments: list[Segment]) -> None:
    a = plan_article(segments, MockLLMBackend()).segment_indices()
    b = plan_article(segments, MockLLMBackend()).segment_indices()
    assert a == b


def test_plan_empty_segments_raises() -> None:
    with pytest.raises(ValueError):
        plan_article([], MockLLMBackend())


def test_default_max_spots_scales_and_clamps() -> None:
    assert default_max_spots(0) == 0
    assert default_max_spots(2) == 1
    assert default_max_spots(20) == 8  # clamped to the 6-8 target band


def test_plan_drops_hallucinated_spots(segments: list[Segment]) -> None:
    # A misbehaving backend that references non-existent segments / blanks must
    # not break the plan — the bad spots are dropped, the good one survives.
    class NoisyBackend(LLMBackend):
        name = "noisy"
        requires_key = False

        def plan(self, segments, *, max_spots, article_title=None):  # type: ignore[override]
            return [
                {"after_segment_idx": 999, "depicts": "幻觉段落", "reason": ""},
                {"after_segment_idx": 1, "depicts": "", "reason": "空描述"},
                {"after_segment_idx": 1, "depicts": "真实的一段配图", "reason": "ok"},
                {"after_segment_idx": 1, "depicts": "重复段落", "reason": "dup"},
            ]

    plan = plan_article(segments, NoisyBackend())
    assert plan.count == 1
    assert plan.spots[0].after_segment_idx == 1
    assert plan.spots[0].depicts == "真实的一段配图"


def test_degenerate_all_transition_article_still_plans() -> None:
    # If every paragraph reads like a transition, the mock falls back to the
    # longest paragraph so the plan is never empty for real text.
    segs = [
        Segment(idx=0, text="首先，", role=SegmentRole.TRANSITION),
        Segment(
            idx=1,
            text="所以接下来我们简单过一下，这是一段比较长的承接文字内容用于兜底测试场景。",
            role=SegmentRole.TRANSITION,
        ),
    ]
    plan = plan_article(segs, MockLLMBackend())
    assert plan.count >= 1


# --------------------------------------------------------------------------- #
# config.py — keyless / mock toggle
# --------------------------------------------------------------------------- #

def test_config_defaults_to_real_backend_names() -> None:
    cfg = Config()
    assert cfg.llm_backend == "deepseek"
    assert cfg.image_backend == "tongyi-wanxiang"


def test_config_force_mock_pins_mock(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DUANHUI_MOCK", raising=False)
    cfg = Config.load(path=tmp_path / "config.yaml", force_mock=True)
    assert cfg.uses_mock_llm()
    assert cfg.effective_llm_backend() == "mock"


def test_config_real_backend_without_key_degrades_to_mock(tmp_path, monkeypatch) -> None:
    # A configured real backend with no key available must degrade to the
    # keyless mock rather than crash — the dry-run must always work.
    for var in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "DUANHUI_MOCK"):
        monkeypatch.delenv(var, raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("llm_backend: deepseek\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.uses_mock_llm()


def test_config_env_key_enables_real_backend(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DUANHUI_MOCK", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-123")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("llm_backend: deepseek\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert not cfg.uses_mock_llm()
    assert cfg.effective_llm_backend() == "deepseek"
    assert cfg.llm_api_key == "sk-test-123"


def test_config_roundtrip_save_load(tmp_path) -> None:
    cfg_path = tmp_path / "config.yaml"
    Config(llm_backend="qwen", image_backend="kling", max_spots=5).save(cfg_path)
    loaded = Config.load(path=cfg_path)
    assert loaded.llm_backend == "qwen"
    assert loaded.image_backend == "kling"
    assert loaded.max_spots == 5


def test_config_full_keyless_pipeline(sample_text: str, tmp_path) -> None:
    # The headline m1 guarantee: from raw article to a plan with zero keys.
    cfg = Config.load(path=tmp_path / "nope.yaml", force_mock=True)
    backend = make_llm_backend(cfg.effective_llm_backend())
    plan = plan_article(segment_article(sample_text), backend)
    assert not plan.is_empty()


def test_config_image_backend_degrades_to_mock(tmp_path, monkeypatch) -> None:
    for var in ("DASHSCOPE_API_KEY", "KLING_API_KEY", "DUANHUI_MOCK"):
        monkeypatch.delenv(var, raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("image_backend: tongyi-wanxiang\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.uses_mock_image()
    assert cfg.effective_image_backend() == "mock"


# --------------------------------------------------------------------------- #
# style.py — the m2 style lock
# --------------------------------------------------------------------------- #

def test_style_pack_loads_with_required_fields(style: StyleLock) -> None:
    assert style.pack_id == "guaidan"
    assert style.consistency_seed  # the shared seed must be present
    assert "{{ depicts }}" in style.style_preamble
    assert style.aspect_wh() == (16, 9)


def test_style_render_prompt_injects_depicts(style: StyleLock) -> None:
    prompt = style.render_prompt("一个塞满便签的背包")
    assert "一个塞满便签的背包" in prompt
    assert "白" in prompt or "纯白" in prompt  # the locked 白底 motif leaks through
    assert "{{" not in prompt  # template fully rendered


def test_styled_spots_share_one_seed(segments: list[Segment], style: StyleLock) -> None:
    plan = plan_article(segments, MockLLMBackend(), article_title="待办清单")
    styled = build_styled_spots(plan, style)
    assert len(styled) == plan.count
    # the consistency lock: every spot in one article carries the SAME seed.
    seeds = {s.seed for s in styled}
    assert seeds == {style.consistency_seed}
    # filenames are ordered + unique.
    names = [s.filename for s in styled]
    assert names == sorted(names)
    assert len(set(names)) == len(names)
    for s in styled:
        assert s.depicts in s.prompt


# --------------------------------------------------------------------------- #
# backends/image.py — the m2 render
# --------------------------------------------------------------------------- #

def test_mock_image_backend_needs_no_key() -> None:
    backend = make_image_backend("mock")
    assert isinstance(backend, MockImageBackend)
    assert backend.requires_key is False


def test_image_registry_lists_mock_and_real() -> None:
    names = available_image_backends()
    assert names[0] == "mock"
    for real in ("tongyi-wanxiang", "kling", "jimeng", "seedream"):
        assert real in names


def test_unknown_image_backend_raises() -> None:
    with pytest.raises(KeyError):
        make_image_backend("not-a-real-image-model")


def test_real_image_backend_without_key_raises() -> None:
    with pytest.raises(ValueError):
        make_image_backend("tongyi-wanxiang", api_key="")


def test_is_mock_image_recognises_aliases() -> None:
    for alias in ("mock", "", "none", "off"):
        assert is_mock_image(alias)
    assert not is_mock_image("kling")


def _read_png_size(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    w, h = struct.unpack(">II", raw[16:24])
    return w, h


def test_mock_render_writes_valid_consistent_pngs(
    segments: list[Segment], style: StyleLock, tmp_path
) -> None:
    plan = plan_article(segments, MockLLMBackend())
    styled = build_styled_spots(plan, style)
    rendered = MockImageBackend().render_batch(styled, tmp_path / "images")

    assert len(rendered) == len(styled)
    for r in rendered:
        assert r.path.exists()
        w, h = _read_png_size(r.path)
        # 16:9 canvas → square-free landscape.
        assert (w, h) == (480, 270)
    # batch reads as one family: all near-白底 (every channel high).
    # (we only assert validity + shape here; consistency is exercised by the
    # shared-seed test above.)
    assert len({r.path.name for r in rendered}) == len(rendered)


def test_mock_render_is_deterministic(
    segments: list[Segment], style: StyleLock, tmp_path
) -> None:
    plan = plan_article(segments, MockLLMBackend())
    styled = build_styled_spots(plan, style)
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    MockImageBackend().render_batch(styled, a_dir)
    MockImageBackend().render_batch(styled, b_dir)
    for spot in styled:
        assert (a_dir / spot.filename).read_bytes() == (b_dir / spot.filename).read_bytes()


# --------------------------------------------------------------------------- #
# export.py — the m3 bundle
# --------------------------------------------------------------------------- #

def test_export_bundle_has_three_artifacts(
    segments: list[Segment], style: StyleLock, tmp_path
) -> None:
    plan = plan_article(segments, MockLLMBackend(), article_title="待办清单")
    styled = build_styled_spots(plan, style)
    rendered = MockImageBackend().render_batch(styled, tmp_path / "out" / "images")
    bundle = export_bundle(
        segments, styled, rendered, style, tmp_path / "out", article_title="待办清单"
    )

    assert bundle.placement_map_path.exists()
    assert bundle.annotated_md_path.exists()
    assert bundle.image_count == len(styled)
    for p in bundle.image_paths:
        assert p.exists()


def test_placement_map_json_is_valid_and_joins_back(
    segments: list[Segment], style: StyleLock, tmp_path
) -> None:
    plan = plan_article(segments, MockLLMBackend(), article_title="待办清单")
    styled = build_styled_spots(plan, style)
    rendered = MockImageBackend().render_batch(styled, tmp_path / "out" / "images")
    bundle = export_bundle(
        segments, styled, rendered, style, tmp_path / "out", article_title="待办清单"
    )

    data = json.loads(bundle.placement_map_path.read_text(encoding="utf-8"))
    # round-trips through the typed model.
    pm = PlacementMap(**data)
    assert pm.image_count == len(styled)
    assert pm.consistency_seed == style.consistency_seed

    valid_idx = {s.idx for s in segments}
    files = {p.name for p in bundle.image_paths}
    for entry in pm.entries:
        # spot → file → segment is a real, resolvable join.
        assert entry.after_segment_idx in valid_idx
        assert entry.file in files
        assert entry.seed == style.consistency_seed


def test_annotated_md_marks_every_image(
    segments: list[Segment], style: StyleLock, tmp_path
) -> None:
    plan = plan_article(segments, MockLLMBackend(), article_title="待办清单")
    styled = build_styled_spots(plan, style)
    rendered = MockImageBackend().render_batch(styled, tmp_path / "out" / "images")
    bundle = export_bundle(
        segments, styled, rendered, style, tmp_path / "out", article_title="待办清单"
    )
    md = bundle.annotated_md_path.read_text(encoding="utf-8")
    # one marker + one inline image link per illustration.
    for spot in styled:
        assert spot.filename in md
        assert f"图{spot.order + 1}" in md


# --------------------------------------------------------------------------- #
# cli.py — the user-facing entry point (keyless end-to-end)
# --------------------------------------------------------------------------- #

def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "duanhui" in result.stdout


def test_cli_backends_lists_families() -> None:
    result = runner.invoke(app, ["backends"])
    assert result.exit_code == 0
    assert "deepseek" in result.stdout
    assert "tongyi-wanxiang" in result.stdout


def test_cli_dry_run_prints_plan_and_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(
        app, ["run", str(SAMPLE), "--dry-run", "-o", str(out_dir)]
    )
    assert result.exit_code == 0, result.stdout
    assert "配图计划" in result.stdout
    # dry-run renders nothing.
    assert not out_dir.exists()


def test_cli_full_run_produces_bundle(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    out_dir = tmp_path / "out"
    result = runner.invoke(app, ["run", str(SAMPLE), "-o", str(out_dir)])
    assert result.exit_code == 0, result.stdout

    assert (out_dir / "placement_map.json").exists()
    assert (out_dir / "annotated.md").exists()
    pngs = list((out_dir / "images").glob("illustration_*.png"))
    assert len(pngs) >= 1
    for p in pngs:
        assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_cli_missing_file_errors(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_MOCK", "1")
    result = runner.invoke(app, ["run", str(tmp_path / "nope.md"), "--dry-run"])
    assert result.exit_code == 2


def test_cli_init_writes_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DUANHUI_HOME", str(tmp_path))
    # accept defaults for both backends, leave both keys blank → stays keyless.
    result = runner.invoke(app, ["init"], input="\n\n\n\n")
    assert result.exit_code == 0, result.stdout
    cfg_file = tmp_path / "config.yaml"
    assert cfg_file.exists()
    loaded = Config.load(path=cfg_file)
    assert loaded.llm_backend  # a backend was recorded
    assert loaded.uses_mock_llm()  # no key → still mock
