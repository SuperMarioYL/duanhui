"""v0.4.0 regression tests — three backend-robustness fixes + version bump.

All run keyless / offline (httpx is mocked) so CI stays green and the fixes are
reproducible without a real API key.

* **fix-llm-response-fragile-parse** — a non-standard chat response (error body,
  null content, or non-JSON prose) now raises a clear ``RuntimeError`` instead
  of an opaque KeyError / IndexError / AttributeError.
* **fix-image-download-no-status-check** — a failed image download now raises a
  clear ``RuntimeError`` instead of silently writing a corrupt PNG.
* **fix-image-aspect-zero-division** — a zero-dimension aspect ratio no longer
  crashes the real image backends with ``ZeroDivisionError`` (falls back to
  16:9, matching the mock's existing guard).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from duanhui import __version__
from duanhui.backends.image import (
    KlingBackend,
    MockImageBackend,
    StyledSpot,
    TongyiWanxiangBackend,
)
from duanhui.backends.llm import DeepSeekBackend
from duanhui.cli import app
from duanhui.segment import Segment, SegmentRole

runner = CliRunner()

IMG_URL = "https://cdn.example.com/img.png"


def _spot(aspect: str = "16:9") -> StyledSpot:
    """A minimal render-ready spot — only prompt/seed/aspect are read here."""
    return StyledSpot(
        order=0,
        after_segment_idx=0,
        depicts="一个塞满便签的背包",
        prompt="白底怪诞手绘 一个塞满便签的背包",
        seed=73219,
        aspect_ratio=aspect,
        pack_id="guaidan",
    )


def _segs() -> list[Segment]:
    return [Segment(idx=0, text="这是一段引言介绍主题。", role=SegmentRole.INTRO)]


# --------------------------------------------------------------------------- #
# fakes — return real httpx.Response objects so raise_for_status / .json /
# .content behave exactly as in production (no hand-rolled stubs that hide the
# very bug being fixed).
# --------------------------------------------------------------------------- #


def _json_response(payload: Any, method: str = "POST") -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        request=httpx.Request(method, "https://example.com"),
    )


class _LLMClient:
    """Stand-in for ``httpx.Client`` serving one canned chat-completion."""

    def __init__(self, payload: Any):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return _json_response(self._payload)


def _mock_llm(monkeypatch, payload: Any) -> None:
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _LLMClient(payload))


class _ImageClient:
    """Stand-in for ``httpx.Client`` for the image fetch path.

    POST returns ``post_payload`` as JSON; GET on a ``/tasks/`` URL pops the next
    task payload; GET on the image URL either succeeds (image bytes) or fails
    (a real 4xx ``httpx.Response`` whose ``raise_for_status`` raises) when
    ``fail_image`` is set.
    """

    def __init__(
        self,
        *,
        post_payload: Any,
        task_payloads: list = (),
        fail_image: bool = False,
        image_bytes: bytes = b"\x89PNG\r\n\x1a\nFAKE",
    ):
        self._post = post_payload
        self._task = list(task_payloads)
        self._fail = fail_image
        self._image = image_bytes
        self.get_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return _json_response(self._post)

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if "/tasks/" in url:
            return _json_response(self._task.pop(0))
        if self._fail:
            return httpx.Response(404, request=httpx.Request("GET", url))
        return httpx.Response(
            200, content=self._image, request=httpx.Request("GET", url)
        )


def _mock_image(monkeypatch, **kwargs) -> _ImageClient:
    import time

    fake = _ImageClient(**kwargs)
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    # the tongyi polling loop sleeps between polls — collapse it for an instant test
    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)
    return fake


# --------------------------------------------------------------------------- #
# fix-llm-response-fragile-parse
# --------------------------------------------------------------------------- #


def test_llm_error_body_raises_clear_runtime_error(monkeypatch) -> None:
    # v0.4.0 bug repro: a 200-with-error-body (no `choices`) used to raise an
    # opaque KeyError out of `data["choices"]`; now a clear RuntimeError.
    _mock_llm(monkeypatch, {"code": "InvalidApiKey", "message": "bad key"})
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="deepseek.*no usable content"):
        backend.plan(_segs(), max_spots=4, article_title="t")


def test_llm_null_content_raises_clear_runtime_error(monkeypatch) -> None:
    # content: null (refusal/filtered turn) used to raise AttributeError in
    # _extract_json_array (.strip on None); now a clear RuntimeError.
    _mock_llm(monkeypatch, {"choices": [{"message": {"content": None}}]})
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="no usable content"):
        backend.plan(_segs(), max_spots=4)


def test_llm_non_json_prose_raises_clear_runtime_error(monkeypatch) -> None:
    # Conversational prose (no JSON array) used to raise an uncaught ValueError;
    # now it is wrapped in a clear RuntimeError.
    _mock_llm(monkeypatch, {"choices": [{"message": {"content": "我无法帮忙"}}]})
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="could not parse"):
        backend.plan(_segs(), max_spots=4)


def test_llm_valid_json_still_returns_spots(monkeypatch) -> None:
    # regression guard: a well-formed response still parses into raw spots.
    content = json.dumps(
        [{"after_segment_idx": 0, "depicts": "测试配图", "reason": "ok"}],
        ensure_ascii=False,
    )
    _mock_llm(monkeypatch, {"choices": [{"message": {"content": content}}]})
    raw = DeepSeekBackend(api_key="sk-test").plan(_segs(), max_spots=4)
    assert len(raw) == 1
    assert raw[0]["depicts"] == "测试配图"


# --------------------------------------------------------------------------- #
# fix-image-download-no-status-check
# --------------------------------------------------------------------------- #


def test_kling_download_failure_raises(monkeypatch) -> None:
    # v0.4.0 bug repro: a failed image download used to write the error body as a
    # corrupt PNG; now it raises a clear RuntimeError.
    _mock_image(
        monkeypatch,
        post_payload={"data": [{"url": IMG_URL}]},
        fail_image=True,
    )
    backend = KlingBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="kling.*image download failed"):
        backend._request_image(_spot())


def test_kling_download_success_returns_bytes(monkeypatch) -> None:
    # regression guard: a successful download still returns the image bytes.
    _mock_image(
        monkeypatch,
        post_payload={"data": [{"url": IMG_URL}]},
        image_bytes=b"\x89PNG\r\n\x1a\nFAKE",
    )
    backend = KlingBackend(api_key="sk-test")
    assert backend._request_image(_spot()) == b"\x89PNG\r\n\x1a\nFAKE"


def test_tongyi_download_failure_raises(monkeypatch) -> None:
    # the DEFAULT image backend: after the async poll succeeds, a failed final
    # fetch now raises instead of writing a corrupt PNG.
    _mock_image(
        monkeypatch,
        post_payload={"output": {"task_id": "T1", "task_status": "PENDING"}},
        task_payloads=[
            {
                "output": {
                    "task_id": "T1",
                    "task_status": "SUCCEEDED",
                    "results": [{"url": IMG_URL}],
                }
            }
        ],
        fail_image=True,
    )
    backend = TongyiWanxiangBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="tongyi-wanxiang.*image download failed"):
        backend._request_image(_spot())


# --------------------------------------------------------------------------- #
# fix-image-aspect-zero-division
# --------------------------------------------------------------------------- #


def test_aspect_size_zero_dimension_falls_back() -> None:
    # v0.4.0 bug repro: a zero-dimension aspect ratio ("0:9") used to divide by
    # zero in the real backends (the mock guarded it); now it falls back to 16:9.
    assert KlingBackend._aspect_size(_spot("0:9")) == "1280*720"


def test_aspect_size_zero_height_falls_back() -> None:
    # symmetric: a zero height ("16:0") also falls back instead of crashing.
    assert KlingBackend._aspect_size(_spot("16:0")) == "1280*720"


def test_aspect_size_valid_unchanged() -> None:
    # regression guard: a normal ratio still computes correctly.
    assert KlingBackend._aspect_size(_spot("16:9")) == "1280*720"
    assert KlingBackend._aspect_size(_spot("9:16")).split("*")[0] == "1280"


def test_mock_canvas_still_guards_zero() -> None:
    # the mock's existing guard still produces a valid canvas (consistency
    # between the two backend families).
    assert MockImageBackend()._canvas(_spot("0:9")) == (480, 270)


# --------------------------------------------------------------------------- #
# version bump
# --------------------------------------------------------------------------- #


def test_version_is_040() -> None:
    assert __version__ == "0.4.0"


def test_cli_version_reports_040() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stdout
    assert "0.4.0" in result.stdout
