"""LLM placement backends — the model family that decides *where* to illustrate.

A placement backend receives the article's role-tagged segments and returns a
list of *raw spot dicts* (``{"after_segment_idx", "depicts", "reason"}``).
:mod:`duanhui.placement` validates those into typed ``Spot`` objects, so the
backend itself stays a thin adapter.

Three real adapters are registered behind the same interface
(``deepseek`` / ``qwen`` / ``glm``); each calls its provider's
OpenAI-compatible chat endpoint via ``httpx`` and asks for strict JSON. None of
them is hardcoded as the only path — the default is chosen in
:mod:`duanhui.config`.

The keyless path is :class:`MockLLMBackend`: a fully deterministic, rule-based
planner that needs no API key and powers the ``--dry-run`` demo and CI. The m1
milestone is "done" when a real sample article yields a sensible, deterministic
plan through this mock with **zero keys**.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Sequence

from duanhui.segment import Segment, SegmentRole

__all__ = [
    "LLMBackend",
    "MockLLMBackend",
    "DeepSeekBackend",
    "QwenBackend",
    "GLMBackend",
    "RawSpot",
]

# A backend yields these loosely-typed dicts; placement.py validates them.
RawSpot = Dict[str, Any]


class LLMBackend(ABC):
    """Abstract placement-planning backend.

    Implementations turn role-tagged segments into a list of raw spot dicts.
    The interface is intentionally tiny so that swapping providers — or the
    keyless mock — never touches the pipeline.
    """

    #: registry name, e.g. ``"deepseek"``. Set on each concrete subclass.
    name: str = "abstract"

    #: whether this backend needs an API key to run.
    requires_key: bool = True

    @abstractmethod
    def plan(
        self,
        segments: Sequence[Segment],
        *,
        max_spots: int,
        article_title: str | None = None,
    ) -> List[RawSpot]:
        """Return up to ``max_spots`` raw illustration spots for the article."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Mock backend — the keyless, deterministic m1 engine.
# --------------------------------------------------------------------------- #

# Stop-words trimmed when distilling a "depicts" phrase from a paragraph, so the
# phrase reads like an illustration brief rather than a sentence fragment.
_DEPICTS_TRIM_RE = re.compile(
    r"^(其实|所以|因此|那么|然后|但是|不过|而且|首先|其次|最后|总之|这|这个|这种|这里)+"
)
# Sentence-ish boundaries used to pick the most illustratable clause.
_CLAUSE_SPLIT_RE = re.compile(r"[，。；！？、,.;!?]")


class MockLLMBackend(LLMBackend):
    """Rule-based, keyless placement planner.

    Strategy (deterministic — same input always yields the same plan):

    1. Prefer ``concept`` and ``example`` segments — those are the paragraphs
       worth a picture. ``intro`` is included once (the opener earns a hero
       illustration); ``transition`` paragraphs are skipped.
    2. Space spots out so two illustrations never sit back-to-back when the
       article is long enough to avoid it.
    3. Cap the count at ``max_spots`` and derive a short Chinese ``depicts``
       phrase plus a human-readable ``reason`` from each chosen paragraph.

    The plan is good enough to be *useful as a preview* and, crucially, costs
    nothing to produce — which is the whole point of the dry-run.
    """

    name = "mock"
    requires_key = False

    # roles ranked by how much an illustration helps the reader.
    _ROLE_PRIORITY = {
        SegmentRole.CONCEPT: 0,
        SegmentRole.EXAMPLE: 1,
        SegmentRole.INTRO: 2,
        SegmentRole.TRANSITION: 9,
    }

    def plan(
        self,
        segments: Sequence[Segment],
        *,
        max_spots: int,
        article_title: str | None = None,
    ) -> List[RawSpot]:
        if max_spots <= 0:
            return []

        # Candidate segments: everything except pure transitions.
        candidates = [
            s for s in segments if s.role is not SegmentRole.TRANSITION
        ]
        if not candidates:
            # Degenerate article (all transitions) — illustrate the longest
            # paragraph so we never return an empty plan for real text.
            candidates = sorted(segments, key=lambda s: s.char_len, reverse=True)[:1]

        # Stable ranking: by role priority, then by paragraph length (longer
        # explanatory paragraphs first), then by original order for ties.
        ranked = sorted(
            candidates,
            key=lambda s: (self._ROLE_PRIORITY.get(s.role, 9), -s.char_len, s.idx),
        )

        chosen: List[Segment] = []
        used_idx: set[int] = set()
        for seg in ranked:
            if len(chosen) >= max_spots:
                break
            # Avoid back-to-back illustrations when the article is long enough.
            if len(segments) > 4 and any(abs(seg.idx - c.idx) <= 1 for c in chosen):
                continue
            chosen.append(seg)
            used_idx.add(seg.idx)

        # If spacing left us short of max_spots, top up ignoring the spacing
        # rule so a dense short article still gets enough illustrations.
        if len(chosen) < min(max_spots, len(candidates)):
            for seg in ranked:
                if len(chosen) >= max_spots:
                    break
                if seg.idx in used_idx:
                    continue
                chosen.append(seg)
                used_idx.add(seg.idx)

        # Emit spots in document order so the preview reads top-to-bottom.
        chosen.sort(key=lambda s: s.idx)
        return [
            {
                "after_segment_idx": seg.idx,
                "depicts": self._depicts_phrase(seg),
                "reason": self._reason(seg),
            }
            for seg in chosen
        ]

    # --- phrase distillation ------------------------------------------------

    @staticmethod
    def _depicts_phrase(seg: Segment) -> str:
        """Distil a short Chinese illustration brief from a paragraph."""
        clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(seg.text) if c.strip()]
        if not clauses:
            return seg.text[:18]
        # Pick the longest of the first two clauses — usually the load-bearing
        # idea, while staying near the top of the paragraph.
        head = max(clauses[:2], key=len)
        head = _DEPICTS_TRIM_RE.sub("", head).strip()
        # Drop a dangling opening quote so the brief never ends mid-quotation.
        head = head.rstrip("“”\"'《（(")
        # Keep it tight — an illustration brief, not a sentence.
        return head[:24] if head else seg.text[:18]

    @staticmethod
    def _reason(seg: Segment) -> str:
        role_zh = {
            SegmentRole.INTRO: "开篇点题，用一张图建立文章基调",
            SegmentRole.CONCEPT: "核心概念段落，配图帮助读者把抽象点视觉化",
            SegmentRole.EXAMPLE: "具体例子/场景，适合用画面呈现",
            SegmentRole.TRANSITION: "承上启下，补一张图缓冲节奏",
        }
        return role_zh.get(seg.role, "适合配图的段落")


# --------------------------------------------------------------------------- #
# Real adapters — OpenAI-compatible chat completions over httpx.
# --------------------------------------------------------------------------- #

# The system prompt asks the model for strict JSON so parsing is robust.
_PLACEMENT_SYSTEM_PROMPT = (
    "你是中文图文创作助手。给定一篇文章的分段（每段含 idx / role / 文本），"
    "请判断在哪些段落之后插入插图，以及每张插图应描绘什么。"
    "只输出 JSON 数组，元素形如 "
    '{"after_segment_idx": int, "depicts": "简短中文画面描述(不超过24字)", "reason": "为什么在此配图"}。'
    "最多输出 {max_spots} 个，按段落顺序排列，优先选择概念/例子段落，跳过纯过渡段。"
    "不要输出 JSON 以外的任何内容。"
)


def _segments_payload(segments: Sequence[Segment]) -> List[Dict[str, Any]]:
    return [{"idx": s.idx, "role": s.role.value, "text": s.text} for s in segments]


def _extract_json_array(content: str) -> List[RawSpot]:
    """Pull the first JSON array out of a chat completion, tolerating fences."""
    content = content.strip()
    # Strip ```json ... ``` fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if fence:
        content = fence.group(1).strip()
    start = content.find("[")
    end = content.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON array")
    parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("LLM JSON was not a list")
    return [p for p in parsed if isinstance(p, dict)]


class _OpenAICompatBackend(LLMBackend):
    """Shared HTTP plumbing for OpenAI-compatible chat-completion providers.

    Concrete providers only need to set ``name``, ``base_url`` and ``model``.
    The network import (``httpx``) is deferred to call time so importing the
    package — and running the keyless mock path — never requires it.
    """

    base_url: str = ""
    model: str = ""

    def __init__(self, api_key: str, *, timeout: float = 60.0) -> None:
        if not api_key:
            raise ValueError(
                f"{self.name} backend requires an API key — run `duanhui init` "
                "or set the matching env var, or stay in --dry-run / mock mode."
            )
        self._api_key = api_key
        self._timeout = timeout

    def plan(
        self,
        segments: Sequence[Segment],
        *,
        max_spots: int,
        article_title: str | None = None,
    ) -> List[RawSpot]:
        import httpx  # deferred: keyless path must import without httpx errors

        system = _PLACEMENT_SYSTEM_PROMPT.replace("{max_spots}", str(max_spots))
        user = json.dumps(
            {
                "title": article_title or "",
                "max_spots": max_spots,
                "segments": _segments_payload(segments),
            },
            ensure_ascii=False,
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json=body,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return _extract_json_array(content)


class DeepSeekBackend(_OpenAICompatBackend):
    """DeepSeek chat backend (``api.deepseek.com``)."""

    name = "deepseek"
    base_url = "https://api.deepseek.com/v1"
    model = "deepseek-chat"


class QwenBackend(_OpenAICompatBackend):
    """Qwen / 通义千问 backend (DashScope OpenAI-compatible endpoint)."""

    name = "qwen"
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model = "qwen-plus"


class GLMBackend(_OpenAICompatBackend):
    """Zhipu GLM / 智谱 backend (``open.bigmodel.cn``)."""

    name = "glm"
    base_url = "https://open.bigmodel.cn/api/paas/v4"
    model = "glm-4-flash"
