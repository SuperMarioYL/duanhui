"""Style lock — fuse the locked 怪诞手绘 pack into a per-spot render prompt.

This is the m2 entry point. It loads ``styles/guaidan.yaml`` (the one native
aesthetic that ships in v0.1), and for every :class:`~duanhui.placement.Spot`
it produces a :class:`StyledSpot`: a ready-to-render prompt that injects the
shared style preamble plus the *same* ``consistency_seed`` into every image of
one article. That shared preamble + seed is the concrete cross-image
consistency mechanism described in MVP plan §2 — it is what makes a batch read
as one hand.

Runtime multi-style switching is explicitly out of scope; the pack *format* is
pluggable (any well-formed YAML pack loads), but only ``guaidan`` ships.

The module is keyless and network-free: it just renders strings, so the m2
prompt build is observable in ``--dry-run`` exactly like the placement plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml
from jinja2 import Environment
from pydantic import BaseModel, Field

from duanhui.placement import PlacementPlan, Spot

__all__ = [
    "StyleLock",
    "StyledSpot",
    "load_style_pack",
    "default_style_path",
    "build_styled_spots",
    "DEFAULT_PACK_ID",
]

DEFAULT_PACK_ID = "guaidan"

# A small, isolated Jinja2 environment for rendering the style preamble. No
# autoescape — the output is a plain-text prompt, not HTML.
_JINJA = Environment(autoescape=False, keep_trailing_newline=False)


def default_style_path(pack_id: str = DEFAULT_PACK_ID) -> Path:
    """Locate the shipped style pack YAML, dev layout or installed layout.

    In a source checkout the packs live at ``<repo>/styles/<pack>.yaml``; in an
    installed wheel they are bundled under ``duanhui/styles/<pack>.yaml`` (see
    ``[tool.hatch.build.targets.wheel.force-include]`` in ``pyproject.toml``).
    Both are probed so the pack is found regardless of how DuanHui was
    installed.
    """
    here = Path(__file__).resolve().parent  # …/duanhui
    candidates = [
        here / "styles" / f"{pack_id}.yaml",          # installed wheel layout
        here.parent / "styles" / f"{pack_id}.yaml",   # source checkout layout
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    # Fall back to the source-checkout path for a clear error message.
    return candidates[1]


class StyleLock(BaseModel):
    """The locked native aesthetic — the ``StyleLock`` primitive from §2.

    A ``StyleLock`` is parsed from a style pack YAML. The two load-bearing
    fields are ``consistency_seed`` (shared by every render in one article) and
    ``style_preamble`` (a Jinja2 template with a ``{{ depicts }}`` slot, fused
    into each per-spot prompt). Everything else is descriptive metadata carried
    through to the export bundle so the author knows which pack produced a batch.
    """

    pack_id: str = Field(..., min_length=1)
    display_name: str = Field(default="")
    aspect_ratio: str = Field(default="16:9")
    motif: str = Field(default="怪诞手绘")
    background: str = Field(default="")
    consistency_seed: int = Field(...)
    style_preamble: str = Field(..., min_length=1)
    negative_prompt: str = Field(default="")
    palette: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}

    def aspect_wh(self) -> tuple[int, int]:
        """Parse ``aspect_ratio`` (e.g. ``"16:9"``) into a ``(w, h)`` ratio.

        Used by the image backends to derive a canvas size. Falls back to 16:9
        if the string is malformed.
        """
        try:
            w_s, h_s = self.aspect_ratio.split(":")
            w, h = int(w_s), int(h_s)
            if w > 0 and h > 0:
                return w, h
        except (ValueError, AttributeError):
            pass
        return 16, 9

    def render_prompt(self, depicts: str) -> str:
        """Fuse the locked style preamble with one spot's ``depicts`` brief."""
        template = _JINJA.from_string(self.style_preamble)
        prompt = template.render(depicts=depicts.strip())
        # collapse incidental whitespace from the YAML block scalar so the
        # prompt is one tidy line per render.
        return " ".join(prompt.split())


class StyledSpot(BaseModel):
    """A placement spot fused with the locked style — ready to render.

    This is the m2 hand-off to the image backend: ``prompt`` carries the locked
    aesthetic + the spot's brief, ``seed`` is the shared consistency seed, and
    the positional fields (``after_segment_idx``, ``order``) let the export
    stage tie each rendered PNG back to its paragraph and to a stable filename.
    """

    order: int = Field(..., ge=0, description="0-based position in the batch")
    after_segment_idx: int = Field(..., ge=0)
    depicts: str = Field(..., min_length=1)
    reason: str = Field(default="")
    prompt: str = Field(..., min_length=1)
    seed: int = Field(...)
    aspect_ratio: str = Field(default="16:9")
    pack_id: str = Field(...)

    @property
    def filename(self) -> str:
        """Stable, sortable PNG filename for this illustration.

        ``illustration_01.png`` … ordered by batch position so the export
        folder lists images in the same order they appear in the article.
        """
        return f"illustration_{self.order + 1:02d}.png"


def load_style_pack(path: Optional[Path] = None) -> StyleLock:
    """Load and validate a style pack YAML into a :class:`StyleLock`.

    Parameters
    ----------
    path:
        Pack file to read. Defaults to the shipped ``guaidan`` pack found by
        :func:`default_style_path`.

    Raises
    ------
    FileNotFoundError
        If the pack file does not exist.
    ValueError
        If the YAML is malformed or missing required fields
        (``consistency_seed`` / ``style_preamble``).
    """
    path = path or default_style_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"style pack not found: {path} — the shipped 'guaidan' pack is missing"
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ValueError(f"style pack {path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"style pack {path} did not parse to a mapping")
    try:
        return StyleLock(**data)
    except Exception as exc:  # pydantic ValidationError or missing field
        raise ValueError(f"style pack {path} is incomplete or invalid: {exc}") from exc


def build_styled_spots(
    plan: PlacementPlan,
    style: Optional[StyleLock] = None,
) -> List[StyledSpot]:
    """Turn a :class:`PlacementPlan` into render-ready :class:`StyledSpot` list.

    Every spot is fused with the *same* locked style preamble and shares the
    *same* ``consistency_seed`` — that is the cross-image consistency lock. The
    output preserves the plan's document order so the batch reads top-to-bottom.
    """
    style = style or load_style_pack()
    return _style_spots(plan.spots, style)


def _style_spots(spots: Sequence[Spot], style: StyleLock) -> List[StyledSpot]:
    styled: List[StyledSpot] = []
    for order, spot in enumerate(spots):
        styled.append(
            StyledSpot(
                order=order,
                after_segment_idx=spot.after_segment_idx,
                depicts=spot.depicts,
                reason=spot.reason,
                prompt=style.render_prompt(spot.depicts),
                seed=style.consistency_seed,
                aspect_ratio=style.aspect_ratio,
                pack_id=style.pack_id,
            )
        )
    return styled
