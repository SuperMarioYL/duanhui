"""Cover mode — derive ONE hero cover illustration for an article.

This is the m5 entry point (base plan §6's explicit v0.2 hedge, and a direct
de-risk of kill-criteria §8's demand-shape falsifier: some authors want a
封面/单图 rather than a whole-article batch). ``duanhui run --cover`` switches
the pipeline from whole-article placement to a single hero cover illustration:

1. distil a short Chinese cover brief (``depicts``) from the article title +
   the opening segment's semantics — reusing the segment + mock-LLM
   heuristics, NOT a separate model;
2. fuse that brief with the *same* locked style pack as the body path (so a
   cover still reads as one hand with a possible later body batch);
3. render ONE PNG (``cover.png``) through the configured image backend
   (mock in CI, keyless);
4. export a small cover bundle: ``cover.png`` + ``placement_map.json`` (one
   cover entry) + ``annotated.md`` (a cover note).

Cover mode is additive: ``duanhui run`` without ``--cover`` is byte-for-byte
unchanged. The cover reuses :class:`~duanhui.style.StyleLock`,
:class:`~duanhui.style.StyledSpot`, and the image backend, so no new
consistency mechanism or rendering path is introduced — only the placement
decision narrows from "N spots across the article" to "one hero spot".
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from pydantic import BaseModel, Field, field_validator

from duanhui.backends.image import ImageBackend, RenderedImage
from duanhui.segment import Segment, SegmentRole
from duanhui.style import StyleLock, StyledSpot, load_style_pack

__all__ = [
    "CoverPlan",
    "plan_cover",
    "build_cover_styled_spot",
    "render_cover",
    "export_cover",
    "COVER_FILENAME",
]

COVER_FILENAME = "cover.png"

# clause-ish boundary for trimming a cover brief off a long intro sentence.
_CLAUSE_SPLIT_RE = re.compile(r"[，。；！？、,.;!?]")


class CoverPlan(BaseModel):
    """One hero cover illustration decision for an article.

    The cover is a single spot: ``depicts`` is a short Chinese brief distilled
    from the article title (+ opening semantics), ``reason`` explains the
    choice, and ``article_title`` is threaded through for the export note.
    """

    depicts: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(default="", max_length=240)
    article_title: str = Field(default="")

    @field_validator("depicts", "reason", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


def _depicts_from_title(article_title: str) -> Optional[str]:
    """Turn a Markdown H1 / title into a tight cover brief, if it carries one."""
    title = (article_title or "").strip().lstrip("#").strip()
    if not title:
        return None
    # a question title ("为什么…？") -> drop the trailing question mark, keep the clause
    title = title.rstrip("？?!.。")
    # if it is a long sentence, keep the first clause — a cover brief, not a paragraph
    head = _CLAUSE_SPLIT_RE.split(title)[0].strip()
    head = head.rstrip("：:，,")
    return head[:24] if head else None


def _depicts_from_intro(segments: Sequence[Segment]) -> Optional[str]:
    """Distil a cover brief from the article's opening paragraph."""
    intro = next((s for s in segments if s.role is SegmentRole.INTRO), None)
    if intro is None or not intro.text:
        return None
    clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(intro.text) if c.strip()]
    if not clauses:
        return None
    # the longest of the first two clauses is usually the load-bearing idea
    head = max(clauses[:2], key=len)
    return head[:24] if head else None


def plan_cover(
    segments: Sequence[Segment],
    article_title: str = "",
) -> CoverPlan:
    """Produce a single hero cover :class:`CoverPlan` for an article.

    The brief is derived deterministically (no model, no key) from the title
    first, falling back to the opening paragraph — so cover mode is as
    reproducible as the body placement plan and runs keyless in CI.

    Raises
    ------
    ValueError
        If ``segments`` is empty.
    """
    if not segments:
        raise ValueError("cannot plan a cover for an empty segment list")

    depicts = _depicts_from_title(article_title) or _depicts_from_intro(segments)
    if not depicts:
        # last resort: the first non-empty segment's leading text
        first = next((s for s in segments if s.text), None)
        depicts = (first.text[:18] if first else "文章封面图")
    reason = "封面/主视觉：取文章标题或开篇语义作为整篇主图，建立基调"
    return CoverPlan(depicts=depicts, reason=reason, article_title=article_title)


def build_cover_styled_spot(plan: CoverPlan, style: StyleLock) -> StyledSpot:
    """Fuse the cover brief with the locked style — a ready-to-render spot.

    The cover shares the pack's ``consistency_seed`` and ``style_preamble``
    exactly like a body spot, so a cover reads as one hand with the (optional)
    body batch. ``order`` is 0 and ``after_segment_idx`` is 0 (the cover sits
    atop the article); the export step writes it to ``cover.png`` explicitly,
    so :attr:`StyledSpot.filename` is not used for the cover.
    """
    return StyledSpot(
        order=0,
        after_segment_idx=0,
        depicts=plan.depicts,
        reason=plan.reason,
        prompt=style.render_prompt(plan.depicts),
        seed=style.consistency_seed,
        aspect_ratio=style.aspect_ratio,
        pack_id=style.pack_id,
    )


def render_cover(
    styled: StyledSpot,
    image_backend: ImageBackend,
    out_dir: "Path",
) -> RenderedImage:
    """Render the single cover PNG to ``<out_dir>/cover.png``."""
    from pathlib import Path  # local to keep the module import-light at top

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / COVER_FILENAME
    return image_backend.render(styled, target)


def export_cover(
    styled: StyledSpot,
    rendered: RenderedImage,
    style: StyleLock,
    out_dir: "Path",
    *,
    article_title: str = "",
) -> "ExportBundle":
    """Assemble the cover export folder and return where it landed.

    Writes ``cover.png`` (already rendered by :func:`render_cover`),
    ``placement_map.json`` with a single cover entry, and an ``annotated.md``
    noting the cover brief. Reuses :class:`~duanhui.export.ExportBundle` for
    the result shape so the CLI reports both modes uniformly.
    """
    import json
    from pathlib import Path

    from duanhui.export import ExportBundle, PlacementEntry, PlacementMap

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    entry = PlacementEntry(
        order=0,
        file=COVER_FILENAME,
        after_segment_idx=styled.after_segment_idx,
        depicts=styled.depicts,
        reason=styled.reason,
        prompt=styled.prompt,
        seed=styled.seed,
    )
    placement_map = PlacementMap(
        article_title=article_title,
        pack_id=style.pack_id,
        consistency_seed=style.consistency_seed,
        aspect_ratio=style.aspect_ratio,
        image_count=1,
        entries=[entry],
    )
    placement_map_path = out_dir / "placement_map.json"
    placement_map_path.write_text(placement_map.to_json(), encoding="utf-8")

    title_line = f"# {article_title}\n\n" if article_title else ""
    annotated = (
        f"{title_line}"
        f"> 本文由「段绘」封面模式生成主视觉：1 张 `{style.pack_id}` 风格封面图。\n"
        f"把 `{COVER_FILENAME}` 贴在文章封面位置即可。\n\n"
        f"**▸ 封面图** — `{COVER_FILENAME}`：{styled.depicts}\n\n"
        f"![封面：{styled.depicts}]({COVER_FILENAME})\n"
    )
    annotated_md_path = out_dir / "annotated.md"
    annotated_md_path.write_text(annotated, encoding="utf-8")

    return ExportBundle(
        out_dir=out_dir.resolve(),
        images_dir=images_dir.resolve(),
        placement_map_path=placement_map_path.resolve(),
        annotated_md_path=annotated_md_path.resolve(),
        image_paths=[rendered.path.resolve()],
    )
