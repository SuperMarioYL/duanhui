"""Export bundle — assemble a drop-in folder for 公众号 / 小红书.

This is the m3 entry point. Given the article's segments, the rendered images
(one per styled spot), and the style lock, it writes a single export folder the
author drops straight into a 公众号 / 小红书 editor::

    out/
    ├── images/
    │   ├── illustration_01.png
    │   ├── illustration_02.png
    │   └── …
    ├── placement_map.json     # spot → file → segment idx (the machine-readable map)
    └── annotated.md           # the article with [图N: …] markers where each PNG goes

``placement_map.json`` is the ``ExportBundle`` primitive from MVP plan §2
(``{ images[], placement_map.json (spot→file→segment), annotated.md }``).
``annotated.md`` is what a non-coder actually reads: it reproduces the original
article and, right after each illustrated paragraph, inserts a clear marker
naming the PNG to paste and what it depicts.

The whole stage is keyless and pure-Python: it only moves bytes and renders
text, so the full pipeline — segment → place → style-lock → render → export —
runs end to end with **zero API keys**.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Sequence

from pydantic import BaseModel, Field

from duanhui.backends.image import RenderedImage
from duanhui.segment import Segment
from duanhui.style import StyleLock, StyledSpot

__all__ = [
    "PlacementEntry",
    "PlacementMap",
    "ExportBundle",
    "export_bundle",
]


class PlacementEntry(BaseModel):
    """One row of ``placement_map.json``: an image tied to a paragraph.

    ``after_segment_idx`` is the join key back to the article's segments, so a
    downstream tool (or the author) can answer "which PNG goes after which
    paragraph?" without re-running the pipeline.
    """

    order: int = Field(..., ge=0)
    file: str = Field(..., description="image filename, relative to images/")
    after_segment_idx: int = Field(..., ge=0)
    depicts: str = Field(...)
    reason: str = Field(default="")
    prompt: str = Field(..., description="the exact style-locked render prompt")
    seed: int = Field(..., description="shared consistency seed for the batch")


class PlacementMap(BaseModel):
    """The full machine-readable placement map serialised to JSON."""

    article_title: str = Field(default="")
    pack_id: str = Field(...)
    consistency_seed: int = Field(...)
    aspect_ratio: str = Field(default="16:9")
    image_count: int = Field(..., ge=0)
    entries: List[PlacementEntry] = Field(default_factory=list)

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)


class ExportBundle(BaseModel):
    """Where the finished bundle lives, for the CLI to report back."""

    out_dir: Path
    images_dir: Path
    placement_map_path: Path
    annotated_md_path: Path
    image_paths: List[Path] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def image_count(self) -> int:
        return len(self.image_paths)


def _build_placement_map(
    rendered: Sequence[RenderedImage],
    style: StyleLock,
    *,
    article_title: str,
) -> PlacementMap:
    entries = [
        PlacementEntry(
            order=r.spot.order,
            file=r.path.name,
            after_segment_idx=r.spot.after_segment_idx,
            depicts=r.spot.depicts,
            reason=r.spot.reason,
            prompt=r.spot.prompt,
            seed=r.spot.seed,
        )
        for r in rendered
    ]
    return PlacementMap(
        article_title=article_title,
        pack_id=style.pack_id,
        consistency_seed=style.consistency_seed,
        aspect_ratio=style.aspect_ratio,
        image_count=len(entries),
        entries=entries,
    )


def _render_annotated_md(
    segments: Sequence[Segment],
    styled_spots: Sequence[StyledSpot],
    *,
    article_title: str,
    images_subdir: str = "images",
) -> str:
    """Reproduce the article with an image marker after each illustrated spot.

    The marker is deliberately human-first: a bold ``▸ 图N`` line naming the PNG
    to paste, the paragraph it follows, and what it depicts — so a non-coder can
    place every image by eye. It also embeds a relative ``![…]`` link so editors
    that render Markdown show the placeholder inline.
    """
    # group spots by the segment they follow, preserving batch order.
    by_segment: dict[int, List[StyledSpot]] = {}
    for spot in styled_spots:
        by_segment.setdefault(spot.after_segment_idx, []).append(spot)

    lines: List[str] = []
    if article_title:
        lines.append(f"# {article_title}")
        lines.append("")
    lines.append(
        f"> 本文由「段绘」自动配图：共 {len(styled_spots)} 张同风格插图，"
        f"风格包 `{styled_spots[0].pack_id if styled_spots else '—'}`。"
        "把每个 ▸ 图N 标记处的 PNG 贴进对应段落后即可。"
    )
    lines.append("")

    for seg in segments:
        lines.append(seg.text)
        lines.append("")
        for spot in by_segment.get(seg.idx, []):
            rel = f"{images_subdir}/{spot.filename}"
            lines.append(
                f"**▸ 图{spot.order + 1}（贴在此段之后）** — `{spot.filename}`："
                f"{spot.depicts}"
            )
            lines.append("")
            lines.append(f"![图{spot.order + 1}：{spot.depicts}]({rel})")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_bundle(
    segments: Sequence[Segment],
    styled_spots: Sequence[StyledSpot],
    rendered: Sequence[RenderedImage],
    style: StyleLock,
    out_dir: Path,
    *,
    article_title: str = "",
) -> ExportBundle:
    """Assemble the drop-in export folder and return where everything landed.

    Parameters
    ----------
    segments:
        The article's role-tagged segments (used to rebuild ``annotated.md``).
    styled_spots:
        The style-locked spots (carry depicts / prompt / seed / order).
    rendered:
        The images produced for those spots (``illustration_NN.png`` files).
    style:
        The locked style pack, recorded in ``placement_map.json``.
    out_dir:
        Target folder. Created if missing. ``images/`` is created inside it; the
        backend is expected to have already written PNGs there (or anywhere —
        files are referenced by name in the map).
    article_title:
        Optional title, written atop ``annotated.md`` and into the map.

    Returns
    -------
    ExportBundle
        Absolute paths to the folder and its three artifacts.
    """
    out_dir = Path(out_dir)
    images_dir = out_dir / "images"
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    placement_map = _build_placement_map(
        rendered, style, article_title=article_title
    )
    placement_map_path = out_dir / "placement_map.json"
    placement_map_path.write_text(placement_map.to_json(), encoding="utf-8")

    annotated = _render_annotated_md(
        segments, styled_spots, article_title=article_title
    )
    annotated_md_path = out_dir / "annotated.md"
    annotated_md_path.write_text(annotated, encoding="utf-8")

    return ExportBundle(
        out_dir=out_dir.resolve(),
        images_dir=images_dir.resolve(),
        placement_map_path=placement_map_path.resolve(),
        annotated_md_path=annotated_md_path.resolve(),
        image_paths=[r.path.resolve() for r in rendered],
    )
