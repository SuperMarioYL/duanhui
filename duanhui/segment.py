"""Split a pasted Chinese article into role-tagged paragraph segments.

This is the first stage of the DuanHui pipeline. The input is plain
Markdown / plain text (the plan deliberately keeps OCR / 公众号-HTML / .docx
parsing out of scope), and the output is an ordered list of :class:`Segment`
objects, each carrying:

- ``idx``  — its position in the article (0-based)
- ``text`` — the paragraph's visible text (Markdown heading markers stripped)
- ``role`` — one of ``intro | concept | example | transition``, inferred by a
  small, deterministic, dependency-free heuristic so that downstream placement
  is reproducible with **zero keys**.

The role heuristic is intentionally simple and rule-based: the whole point of
m1 is that a real article yields a *sensible, deterministic* segmentation that
the (mock) placement model can act on without any network call.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List

from pydantic import BaseModel, Field, PrivateAttr

__all__ = ["SegmentRole", "Segment", "segment_article"]


class SegmentRole(str, Enum):
    """The narrative role a paragraph plays in the article.

    The four roles map to the data model in the MVP plan §2
    (``role: intro|concept|example|transition``) and drive which paragraphs
    are worth illustrating.
    """

    INTRO = "intro"
    CONCEPT = "concept"
    EXAMPLE = "example"
    TRANSITION = "transition"


class Segment(BaseModel):
    """One role-tagged paragraph of the article.

    ``idx`` is the stable identity used by the placement plan to point at a
    spot (``Spot.after_segment_idx``), so segmentation must be deterministic.
    """

    idx: int = Field(..., ge=0, description="0-based position in the article")
    text: str = Field(..., description="paragraph text, heading markers stripped")
    role: SegmentRole = Field(..., description="inferred narrative role")

    @property
    def char_len(self) -> int:
        """Number of characters in the paragraph (CJK-aware: 1 char == 1)."""
        return len(self.text)

    @property
    def is_heading(self) -> bool:
        """Whether this paragraph was a Markdown heading in the source."""
        return self._looked_like_heading

    # populated by ``segment_article`` so callers can tell headings apart from
    # ordinary short transition lines without re-parsing the source.
    _looked_like_heading: bool = PrivateAttr(default=False)

    model_config = {"frozen": False}


# --- role heuristic ---------------------------------------------------------
#
# Deterministic, dependency-free. We classify a paragraph from cheap surface
# signals only — no model, no network — so the m1 dry-run is reproducible.

# Markers that strongly signal a concrete, illustratable EXAMPLE paragraph.
_EXAMPLE_MARKERS = (
    "例如",
    "比如",
    "举个例子",
    "举例",
    "譬如",
    "比方说",
    "想象",
    "设想",
    "假设",
    "case",
    "案例",
    "场景",
    "实测",
    "演示",
    "对比",
)

# Markers that signal a TRANSITION / connective paragraph (low illustration
# value — these glue sections together rather than introduce an idea).
_TRANSITION_MARKERS = (
    "首先",
    "其次",
    "然后",
    "接下来",
    "最后",
    "总之",
    "综上",
    "因此",
    "所以",
    "那么",
    "回到",
    "换句话说",
    "小结",
    "总结",
)

# Markers that signal a CONCEPT paragraph — a definition / mechanism / "why"
# explanation, the paragraphs most worth a diagrammatic illustration.
_CONCEPT_MARKERS = (
    "是指",
    "指的是",
    "本质上",
    "原理",
    "机制",
    "为什么",
    "之所以",
    "定义",
    "所谓",
    "核心",
    "关键在于",
    "意味着",
    "其实是",
)

# A heading-shaped line (Markdown ``#`` or a short titley line) is a transition
# unless it carries a concept marker.
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_MD_LIST_RE = re.compile(r"^\s{0,3}([-*+]|\d+[.)])\s+")


def _strip_markdown(line: str) -> str:
    """Strip the leading Markdown heading / list markers from a paragraph."""
    line = _MD_HEADING_RE.sub("", line)
    line = _MD_LIST_RE.sub("", line)
    return line.strip()


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def _classify(text: str, *, idx: int, was_heading: bool) -> SegmentRole:
    """Infer a paragraph's role from deterministic surface signals."""
    # The very first substantive paragraph frames the article.
    if idx == 0:
        return SegmentRole.INTRO

    if _contains_any(text, _EXAMPLE_MARKERS):
        return SegmentRole.EXAMPLE
    if _contains_any(text, _CONCEPT_MARKERS):
        return SegmentRole.CONCEPT

    # Headings without a concept marker, and short connective lines, are
    # transitions. A "short" line here is one under ~24 chars that also carries
    # a transition marker, or any Markdown heading.
    if was_heading:
        return SegmentRole.TRANSITION
    if len(text) <= 24 and _contains_any(text, _TRANSITION_MARKERS):
        return SegmentRole.TRANSITION
    if _contains_any(text, _TRANSITION_MARKERS) and len(text) <= 60:
        return SegmentRole.TRANSITION

    # A reasonably long, marker-free paragraph is treated as a concept body —
    # it is the kind of explanatory prose that benefits from an illustration.
    if len(text) >= 40:
        return SegmentRole.CONCEPT

    return SegmentRole.TRANSITION


def _split_paragraphs(article: str) -> List[tuple[str, bool]]:
    """Split raw article text into ``(paragraph_text, was_heading)`` pairs.

    Paragraphs are blank-line separated. A line that is *only* a Markdown
    heading becomes its own paragraph (so it can be tagged as a transition and
    is never merged into the prose that follows it).
    """
    # Normalise newlines and collapse 3+ blank lines.
    text = article.replace("\r\n", "\n").replace("\r", "\n")
    raw_blocks = re.split(r"\n\s*\n", text)

    out: List[tuple[str, bool]] = []
    for block in raw_blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # A block may start with a heading line followed by body lines; emit
        # the heading on its own and the body as a second paragraph.
        if _MD_HEADING_RE.match(lines[0]) and len(lines) > 1:
            out.append((_strip_markdown(lines[0]), True))
            body = " ".join(_strip_markdown(ln) for ln in lines[1:])
            if body:
                out.append((body, False))
            continue
        was_heading = bool(_MD_HEADING_RE.match(lines[0]) and len(lines) == 1)
        joined = " ".join(_strip_markdown(ln) for ln in lines)
        if joined:
            out.append((joined, was_heading))
    return out


def segment_article(article: str) -> List[Segment]:
    """Split a pasted Chinese article into role-tagged :class:`Segment` list.

    Parameters
    ----------
    article:
        The raw Markdown / plain-text article (as pasted by the author).

    Returns
    -------
    list[Segment]
        Ordered, contiguous segments. The result is fully deterministic for a
        given input, which is what makes the keyless dry-run reproducible.

    Raises
    ------
    ValueError
        If the article has no substantive text.
    """
    if not article or not article.strip():
        raise ValueError("article is empty — paste some Markdown / plain text first")

    pairs = _split_paragraphs(article)
    if not pairs:
        raise ValueError("article contains no substantive paragraphs")

    segments: List[Segment] = []
    for idx, (text, was_heading) in enumerate(pairs):
        role = _classify(text, idx=idx, was_heading=was_heading)
        seg = Segment(idx=idx, text=text, role=role)
        # stash the heading flag (private attr) without widening the schema.
        object.__setattr__(seg, "_looked_like_heading", was_heading)
        segments.append(seg)
    return segments
