"""v0.3.0 regression tests — three bug fixes + version bump.

All run keyless / offline (httpx is mocked for the 通义万相 polling path) so CI
stays green and the fixes are reproducible without a real DashScope key.

* **fix-tongyi-async-no-poll** — the async POST returns a task_id, not the
  image URL; the backend now polls the task endpoint until SUCCEEDED.
* **fix-invalid-llm-name-forces-image-mock** — a typo'd LLM name no longer sets
  the global mock flag, so a valid+keyed image backend stays real.
* **fix-config-max-spots-range-crash** — an out-of-range max_spots in the YAML
  is clamped into [1, 12] instead of crashing Config.load.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from duanhui import __version__
from duanhui.backends.image import StyledSpot, TongyiWanxiangBackend
from duanhui.cli import app
from duanhui.config import MAX_SPOTS_MAX, MAX_SPOTS_MIN, Config

runner = CliRunner()


def _spot() -> StyledSpot:
    """A minimal render-ready spot — only prompt/seed/aspect are read by the
    image backend, so a directly-constructed StyledSpot is enough here."""
    return StyledSpot(
        order=0,
        after_segment_idx=1,
        depicts="一个塞满便签的背包",
        prompt="白底怪诞手绘 一个塞满便签的背包",
        seed=73219,
        aspect_ratio="16:9",
        pack_id="guaidan",
    )


# --------------------------------------------------------------------------- #
# fakes for the 通义万相 async-poll path (no real httpx / network)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, *, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in for ``httpx.Client`` serving canned async-poll responses.

    POST returns ``post_payload``; GET on a ``/tasks/`` URL pops the next
    task status from ``task_payloads``; any other GET returns ``image_bytes``
    (the final image fetch).
    """

    def __init__(self, *, post_payload, task_payloads, image_bytes):
        self._post = post_payload
        self._task = list(task_payloads)
        self._image = image_bytes
        self.get_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        return _FakeResponse(payload=self._post)

    def get(self, url, **kwargs):
        self.get_calls.append(url)
        if "/tasks/" in url:
            return _FakeResponse(payload=self._task.pop(0))
        return _FakeResponse(content=self._image)


def _install_fake_httpx(
    monkeypatch, *, post_payload, task_payloads, image_bytes=b"\x89PNGFAKE"
) -> _FakeClient:
    import httpx
    import time

    fake = _FakeClient(
        post_payload=post_payload,
        task_payloads=task_payloads,
        image_bytes=image_bytes,
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake)
    # the polling loop sleeps between polls — collapse it so the test is instant.
    monkeypatch.setattr(time, "sleep", lambda *_a, **_kw: None)
    return fake


# --------------------------------------------------------------------------- #
# fix-tongyi-async-no-poll
# --------------------------------------------------------------------------- #


def test_tongyi_async_polls_until_succeeded_then_fetches(monkeypatch) -> None:
    # v0.3.0 bug repro: the async POST returns a task_id, not the image URL;
    # the backend must poll the task endpoint until task_status == SUCCEEDED.
    fake = _install_fake_httpx(
        monkeypatch,
        post_payload={
            "output": {"task_id": "TASK-1", "task_status": "PENDING"},
            "request_id": "req-1",
        },
        task_payloads=[
            {"output": {"task_id": "TASK-1", "task_status": "RUNNING"}},
            {
                "output": {
                    "task_id": "TASK-1",
                    "task_status": "SUCCEEDED",
                    "results": [{"url": "https://cdn.example.com/img.png"}],
                }
            },
        ],
        image_bytes=b"\x89PNG\r\n\x1a\nFAKE",
    )

    backend = TongyiWanxiangBackend(api_key="sk-test")
    data = backend._request_image(_spot())
    assert data == b"\x89PNG\r\n\x1a\nFAKE"
    # two task polls (RUNNING, SUCCEEDED) + one final image fetch
    assert len(fake.get_calls) == 3
    assert fake.get_calls[-1] == "https://cdn.example.com/img.png"


def test_tongyi_async_raises_when_task_fails(monkeypatch) -> None:
    _install_fake_httpx(
        monkeypatch,
        post_payload={"output": {"task_id": "TASK-2", "task_status": "PENDING"}},
        task_payloads=[{"output": {"task_id": "TASK-2", "task_status": "FAILED"}}],
    )
    backend = TongyiWanxiangBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="image task failed"):
        backend._request_image(_spot())


def test_tongyi_async_raises_on_timeout(monkeypatch) -> None:
    _install_fake_httpx(
        monkeypatch,
        post_payload={"output": {"task_id": "TASK-3", "task_status": "PENDING"}},
        task_payloads=[{"output": {"task_status": "PENDING"}}],
    )
    backend = TongyiWanxiangBackend(api_key="sk-test")
    backend._POLL_TIMEOUT = 0.0  # expire immediately so the first poll times out
    with pytest.raises(RuntimeError, match="timed out"):
        backend._request_image(_spot())


def test_tongyi_async_raises_when_post_has_no_task_id(monkeypatch) -> None:
    # a malformed POST body with neither results nor task_id is reported, not
    # silently treated as "no image url".
    _install_fake_httpx(
        monkeypatch,
        post_payload={"output": {}, "request_id": "r"},
        task_payloads=[],
    )
    backend = TongyiWanxiangBackend(api_key="sk-test")
    with pytest.raises(RuntimeError, match="no task_id or image url"):
        backend._request_image(_spot())


# --------------------------------------------------------------------------- #
# fix-invalid-llm-name-forces-image-mock
# --------------------------------------------------------------------------- #


def test_invalid_llm_name_does_not_force_image_to_mock(tmp_path, monkeypatch) -> None:
    # v0.3.0 bug repro: a typo'd llm_backend used to set the GLOBAL mock flag in
    # Config.load, which silently forced a valid+keyed image backend to mock
    # too. The flag is now scoped per-backend, so only the LLM side degrades.
    for var in ("DEEPSEEK_API_KEY", "ZHIPU_API_KEY", "DUANHUI_MOCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-real-dashscope")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "llm_backend: bogusllm\nimage_backend: tongyi-wanxiang\n",
        encoding="utf-8",
    )
    cfg = Config.load(path=cfg_path)
    assert cfg.uses_mock_llm()  # typo'd LLM name degrades on its own
    assert not cfg.uses_mock_image()  # image backend stays real + keyed
    assert cfg.effective_image_backend() == "tongyi-wanxiang"
    assert cfg.image_api_key == "sk-real-dashscope"


def test_invalid_image_name_does_not_force_llm_to_mock(tmp_path, monkeypatch) -> None:
    # symmetric guard: a typo'd image_backend must not drag a keyed LLM down.
    for var in ("DASHSCOPE_API_KEY", "JIMENG_API_KEY", "DUANHUI_MOCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-deepseek")
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "llm_backend: deepseek\nimage_backend: bogusimg\n",
        encoding="utf-8",
    )
    cfg = Config.load(path=cfg_path)
    assert cfg.uses_mock_image()  # typo'd image name degrades on its own
    assert not cfg.uses_mock_llm()  # LLM backend stays real + keyed
    assert cfg.effective_llm_backend() == "deepseek"
    assert cfg.llm_api_key == "sk-real-deepseek"


# --------------------------------------------------------------------------- #
# fix-config-max-spots-range-crash
# --------------------------------------------------------------------------- #


def test_max_spots_bounds_constants_match_cli_range() -> None:
    # single source of truth: the Field bounds and the CLI --max-spots agree.
    assert (MAX_SPOTS_MIN, MAX_SPOTS_MAX) == (1, 12)


def test_config_max_spots_above_range_is_clamped(tmp_path, monkeypatch) -> None:
    # v0.3.0 bug repro: max_spots: 15 in the YAML used to raise an uncaught
    # pydantic ValidationError out of Config.load. It now clamps to 12.
    monkeypatch.delenv("DUANHUI_MOCK", raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("max_spots: 15\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.max_spots == MAX_SPOTS_MAX


def test_config_max_spots_below_range_is_clamped(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DUANHUI_MOCK", raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("max_spots: 0\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.max_spots == MAX_SPOTS_MIN


def test_config_max_spots_in_range_unchanged(tmp_path, monkeypatch) -> None:
    # regression guard: the clamp must not distort a valid value.
    monkeypatch.delenv("DUANHUI_MOCK", raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("max_spots: 5\n", encoding="utf-8")
    cfg = Config.load(path=cfg_path)
    assert cfg.max_spots == 5


# --------------------------------------------------------------------------- #
# version bump
# --------------------------------------------------------------------------- #


def test_version_is_030() -> None:
    assert __version__ == "0.4.0"


def test_cli_version_reports_030() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0, result.stdout
    assert "0.4.0" in result.stdout
