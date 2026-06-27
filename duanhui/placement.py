"""The whole-article placement plan — DuanHui's core primitive.

This module owns the typed structure from MVP plan §2::

    PlacementPlan = [ Spot(after_segment_idx, depicts: zh-phrase, reason) ]

A :class:`PlacementPlan` maps Chinese paragraph semantics onto illustration
decisions: *where* to put a picture (``after_segment_idx``) and *what it
depicts* (a short Chinese brief). An :class:`LLMBackend` produces the raw
decisions; this module validates them against the article's segments, dedupes
and clamps to ``max_spots``, and returns a typed, ordered plan.

The function :func:`plan_article` is the m1 entry point: given role-tagged
segments and a backend, it returns a sensible, deterministic plan — and with
:class:`~duanhui.backends.llm.MockLLMBackend` it does so with **zero keys**.
"""

from __future__ import annotations

from typing import List, Sequence

from pydantic import BaseModel, Field, field_validator

from duanhui.backends.llm import LLMBackend, RawSpot
from duanhui.segment import Segment

__all__ = ["Spot", "PlacementPlan", "plan_article", "default_max_spots"]


class Spot(BaseModel):
    """One illustration decision: a picture inserted after a given segment.

    Attributes
    ----------
    after_segment_idx:
        Index of the segment this illustration follows in the article.
    depicts:
        A short Chinese phrase describing what the picture should show. This is
        what later gets fused with the locked style preamble (m2) into a render
        prompt.
    reason:
        Human-readable rationale, surfaced in the dry-run preview so the author
        understands *why* the tool chose this spot before spending credits.
    """

    after_segment_idx: int = Field(..., ge=0)
    depicts: str = Field(..., min_length=1, max_length=120)
    reason: str = Field(default="", max_length=240)

    @field_validator("depicts", "reason", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


class PlacementPlan(BaseModel):
    """An ordered set of illustration spots for one article.

    The plan is the contract between the placement stage and everything
    downstream (style-lock, render, export). It is intentionally a small typed
    object so it can be printed in ``--dry-run``, serialised to
    ``placement_map.json`` in m3, and round-tripped in tests.
    """

    spots: List[Spot] = Field(default_factory=list)
    article_title: str = Field(default="")

    @property
    def count(self) -> int:
        return len(self.spots)

    def is_empty(self) -> bool:
        return not self.spots

    def segment_indices(self) -> List[int]:
        """The segment indices that received an illustration, in order."""
        return [s.after_segment_idx for s in self.spots]


def default_max_spots(n_segments: int) -> int:
    """Heuristic for how many illustrations a ``n``-segment article wants.

    The plan targets a batch of ~6–8 illustrations for a typical 1500–4000 字
    knowledge article. We scale gently with length and clamp to ``[1, 8]`` so a
    short article does not get over-illustrated and a long one stays readable.
    """
    if n_segments <= 0:
        return 0
    # roughly one illustration per ~2 substantive paragraphs.
    target = max(1, round(n_segments / 2))
    return min(8, max(1, target))


def _validate_raw_spots(
    raw: Sequence[RawSpot],
    segments: Sequence[Segment],
    *,
    max_spots: int,
) -> List[Spot]:
    """Coerce backend output into clean, ordered, deduped :class:`Spot` list."""
    valid_indices = {s.idx for s in segments}
    seen: set[int] = set()
    spots: List[Spot] = []

    for item in raw:
        try:
            idx = int(item.get("after_segment_idx"))
        except (TypeError, ValueError):
            continue
        if idx not in valid_indices or idx in seen:
            # drop hallucinated / duplicate segment references defensively.
            continue
        depicts = item.get("depicts")
        if not isinstance(depicts, str) or not depicts.strip():
            continue
        reason = item.get("reason") if isinstance(item.get("reason"), str) else ""
        try:
            spot = Spot(after_segment_idx=idx, depicts=depicts, reason=reason or "")
        except Exception:
            # a single malformed spot must not sink the whole plan.
            continue
        spots.append(spot)
        seen.add(idx)

    spots.sort(key=lambda s: s.after_segment_idx)
    return spots[:max_spots]


def plan_article(
    segments: Sequence[Segment],
    backend: LLMBackend,
    *,
    max_spots: int | None = None,
    article_title: str = "",
) -> PlacementPlan:
    """Produce a typed :class:`PlacementPlan` for an article.

    Parameters
    ----------
    segments:
        Role-tagged segments from :func:`duanhui.segment.segment_article`.
    backend:
        Any :class:`~duanhui.backends.llm.LLMBackend`. Use
        :class:`~duanhui.backends.llm.MockLLMBackend` for the keyless path.
    max_spots:
        Hard cap on illustrations. Defaults to :func:`default_max_spots`.
    article_title:
        Optional title, threaded to the backend and stored on the plan.

    Returns
    -------
    PlacementPlan
        Validated, ordered, deduped. Backend hallucinations (segment indices
        that don't exist, empty briefs, duplicates) are dropped rather than
        raised, so a slightly noisy model still yields a usable plan.

    Raises
    ------
    ValueError
        If ``segments`` is empty.
    """
    if not segments:
        raise ValueError("cannot plan placement for an empty segment list")

    cap = max_spots if max_spots is not None else default_max_spots(len(segments))
    cap = max(0, cap)

    raw = backend.plan(segments, max_spots=cap, article_title=article_title or None)
    spots = _validate_raw_spots(raw, segments, max_spots=cap)
    return PlacementPlan(spots=spots, article_title=article_title)
