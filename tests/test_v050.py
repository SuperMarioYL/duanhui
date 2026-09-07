"""v0.5.0 regression tests — backend POST HTTP-error handling + version bump.

All run keyless / offline (httpx is mocked) so CI stays green and the fixes are
reproducible without a real API key.

* **fix-backend-post-http-error-traceback** — a 4xx/5xx on a backend POST (or
  the tongyi task-poll GET) raised ``httpx.HTTPStatusError``, which is not a
  ``RuntimeError`` and so escaped the CLI's ``except RuntimeError`` guard as an
  opaque traceback. Both backend families now wrap the POST (and the tongyi
  poll GET) so a failed request surfaces as a clear ``RuntimeError``.
* **single-source-of-truth version test** — VERSION file == ``__version__`` ==
  CLI ``--version`` == ``web/site.json`` content_version == CHANGELOG head, so
  a version bump can never leave one surface echoing the old version.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from duanhui import __version__
from duanhui.backends.image import KlingBackend, StyledSpot, TongyiWanxiangBackend
from duanhui.backends.llm import DeepSeekBackend
from duanhui.cli import app
from duanhui.segment import Segment, SegmentRole

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent
IMG_URL = "https://cdn.example.com/img.png"


def _spot(aspect: str = "16:9") -> StyledSpot:
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
# fakes — return real httpx.Response objects so raise_for_status behaves exactly
# as in production (a 401 Response.raise_for_status raises HTTPStatusError).
# --------------------------------------------------------------------------- #


def _resp(status: int, method: str = "POST") -> httpx.Response:
    return httpx.Response(
        status,
        content=b'{"error": "bad key"}',
        request=httpx.Request(method, "https://example.com"),
    )


class _HttpErrorClient:
    """A stand-in for ``httpx.Client`` whose POST/GET always fail.

    POST returns a real non-2xx ``httpx.Response`` (so ``raise_for_status``
    raises ``HTTPStatusError``); GET on a ``/tasks/`` URL does the same, so the
    tongyi poll path is also covered.
    """

    def __init__(self, *, post_status: int = 401, get_status: int = 500):
        self._post_status = post_status
        self._get_status = get_status
        self.get_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return _resp(self._post_status, method="POST")

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if "/tasks/" in url:
            return _resp(self._get_status, method="GET")
        return _resp(self._get_status, method="GET")


def _mock_http_errors(monkeypatch, **kwargs) -> _HttpErrorClient:
    fake = _HttpErrorClient(**kwargs)
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    return fake


# --------------------------------------------------------------------------- #
# fix-backend-post-http-error-traceback — LLM family
# --------------------------------------------------------------------------- #


def test_llm_post_401_raises_runtime_error(monkeypatch) -> None:
    # v0.5.0 bug repro: a 401 (expired key) on the DeepSeek POST raised
    # httpx.HTTPStatusError (not a RuntimeError), so it escaped the CLI's
    # except RuntimeError guard as an opaque traceback. Now a clear RuntimeError.
    _mock_http_errors(monkeypatch, post_status=401)
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="deepseek.*LLM request failed"):
        backend.plan(_segs(), max_spots=4, article_title="t")


def test_llm_post_429_raises_runtime_error(monkeypatch) -> None:
    # a rate-limit 429 is the other common real-user failure on the default
    # deepseek backend; it must surface as a RuntimeError too.
    _mock_http_errors(monkeypatch, post_status=429)
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="deepseek.*LLM request failed"):
        backend.plan(_segs(), max_spots=4)


def test_llm_post_http_error_is_runtime_error_not_httpstatuserror(monkeypatch) -> None:
    # the load-bearing assertion: HTTPStatusError is NOT a RuntimeError subclass,
    # so before the fix the CLI's `except RuntimeError` could not catch it.
    _mock_http_errors(monkeypatch, post_status=401)
    backend = DeepSeekBackend(api_key="sk-test")
    with pytest.raises(RuntimeError) as exc_info:
        backend.plan(_segs(), max_spots=4)
    assert not isinstance(exc_info.value, httpx.HTTPStatusError)


# --------------------------------------------------------------------------- #
# fix-backend-post-http-error-traceback — image family
# --------------------------------------------------------------------------- #


def test_kling_post_401_raises_runtime_error(monkeypatch) -> None:
    # v0.5.0 bug repro: a 401 on the Kling POST raised httpx.HTTPStatusError
    # (not a RuntimeError), escaping the CLI's render guard. Now a clear
    # RuntimeError.
    _mock_http_errors(monkeypatch, post_status=401)
    backend = KlingBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="kling.*request failed"):
        backend._request_image(_spot())


def test_kling_post_500_raises_runtime_error(monkeypatch) -> None:
    _mock_http_errors(monkeypatch, post_status=500)
    backend = KlingBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="kling.*request failed"):
        backend._request_image(_spot())


def test_tongyi_post_401_raises_runtime_error(monkeypatch) -> None:
    # the DEFAULT image backend: a 401 on the submit POST now raises a clear
    # RuntimeError instead of an opaque traceback.
    _mock_http_errors(monkeypatch, post_status=401)
    backend = TongyiWanxiangBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="tongyi-wanxiang.*request failed"):
        backend._request_image(_spot())


def test_tongyi_poll_failure_raises_runtime_error(monkeypatch) -> None:
    # the submit POST succeeds but the async task-poll GET fails (a 500 on the
    # DashScope task endpoint); that GET also used to raise HTTPStatusError
    # outside the CLI guard.
    import time

    class _PollFailClient:
        def __init__(self):
            self.get_calls: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, **kwargs):
            # submit succeeds, returns a task_id to poll.
            return httpx.Response(
                200,
                content=json.dumps(
                    {"output": {"task_id": "T1", "task_status": "PENDING"}}
                ).encode("utf-8"),
                request=httpx.Request("POST", url),
            )

        def get(self, url, **kwargs):
            self.get_calls.append(url)
            return _resp(500, method="GET")

    fake = _PollFailClient()
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)
    backend = TongyiWanxiangBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="tongyi-wanxiang.*task poll failed"):
        backend._request_image(_spot())


# --------------------------------------------------------------------------- #
# regression guards — the wrapped POST must still return the response on success
# --------------------------------------------------------------------------- #


def _json_response(payload: Any, method: str = "POST") -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        request=httpx.Request(method, "https://example.com"),
    )


class _SuccessClient:
    """POST returns a canned success body; GET on the image URL returns bytes."""

    def __init__(self, post_payload: Any, image_bytes: bytes = b"\x89PNG\r\n\x1a\nFAKE"):
        self._post = post_payload
        self._image = image_bytes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return _json_response(self._post)

    def get(self, url, **kwargs):
        return httpx.Response(
            200, content=self._image, request=httpx.Request("GET", url)
        )


def test_kling_post_success_still_returns_bytes(monkeypatch) -> None:
    # regression guard: a successful POST still returns the image bytes (the
    # _post_json helper must not swallow a 2xx).
    fake = _SuccessClient(post_payload={"data": [{"url": IMG_URL}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    backend = KlingBackend(api_key="sk-test")
    assert backend._request_image(_spot()) == b"\x89PNG\r\n\x1a\nFAKE"


def test_llm_post_success_still_parses(monkeypatch) -> None:
    # regression guard: a successful LLM POST still parses into raw spots.
    content = json.dumps(
        [{"after_segment_idx": 0, "depicts": "测试配图", "reason": "ok"}],
        ensure_ascii=False,
    )
    fake = _SuccessClient(post_payload={"choices": [{"message": {"content": content}}]})
    monkeypatch.setattr(httpx, "Client", lambda **kw: fake)
    raw = DeepSeekBackend(api_key="sk-test").plan(_segs(), max_spots=4)
    assert len(raw) == 1
    assert raw[0]["depicts"] == "测试配图"


# --------------------------------------------------------------------------- #
# single-source-of-truth version test
# --------------------------------------------------------------------------- #


def test_version_surfaces_agree() -> None:
    """VERSION file == __version__ == CLI --version == site.content_version ==
    CHANGELOG head. Fails on the shipped v0.4.0 tag (surfaces were 0.4.0 /
    site had no content_version), proving the bump touched every surface."""
    expected = "0.5.0"

    # VERSION file
    version_file = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version_file == expected, f"VERSION file is {version_file!r}"

    # __version__
    assert __version__ == expected, f"__version__ is {__version__!r}"

    # CLI --version
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
    assert f"## [{expected}]" in changelog, "CHANGELOG head missing v0.5.0 entry"
    # the v0.5.0 entry must be above the v0.4.0 entry (newest first).
    assert changelog.index(f"## [{expected}]") < changelog.index("## [0.4.0]")
