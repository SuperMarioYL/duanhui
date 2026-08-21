"""Image backends — the model family that renders one style-locked spot to PNG.

An image backend receives a :class:`~duanhui.style.StyledSpot` (locked prompt +
shared consistency seed) and writes a PNG to disk. Four real adapters are
registered behind one interface (``tongyi-wanxiang`` / ``kling`` / ``jimeng`` /
``seedream``); each posts to its provider's text-to-image endpoint via ``httpx``
and saves the returned image. None is hardcoded as the only path — the default
is chosen in :mod:`duanhui.config`.

The keyless path is :class:`MockImageBackend`: it writes a **real, valid PNG**
placeholder for every spot using only the Python standard library (``zlib`` +
``struct`` — no Pillow dependency, which keeps the keyless install tiny and the
plan's ``key_deps`` honoured). The mock derives each placeholder's tint from the
shared ``consistency_seed`` plus the spot's depicts phrase, so a batch is
visibly one family while individual spots still differ — exactly the property
m2 promises ("a consistent batch of PNGs, keyless in CI").
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List

from duanhui.style import StyledSpot

__all__ = [
    "ImageBackend",
    "MockImageBackend",
    "TongyiWanxiangBackend",
    "KlingBackend",
    "JimengBackend",
    "SeeDreamBackend",
    "RenderedImage",
]


class RenderedImage:
    """The result of rendering one spot: where the PNG landed plus its prompt.

    Returned by :meth:`ImageBackend.render` so the export stage can map a file
    back to the spot/segment it illustrates without re-deriving anything.
    """

    __slots__ = ("path", "spot")

    def __init__(self, path: Path, spot: StyledSpot) -> None:
        self.path = path
        self.spot = spot

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RenderedImage(path={self.path.name!r}, order={self.spot.order})"


class ImageBackend(ABC):
    """Abstract image-generation backend.

    Implementations render one :class:`StyledSpot` to a PNG file. The interface
    is intentionally tiny so swapping providers — or the keyless mock — never
    touches the render loop in :meth:`render_batch`.
    """

    #: registry name, e.g. ``"tongyi-wanxiang"``. Set on each concrete subclass.
    name: str = "abstract"

    #: whether this backend needs an API key to run.
    requires_key: bool = True

    @abstractmethod
    def render(self, spot: StyledSpot, out_path: Path) -> RenderedImage:
        """Render a single styled spot to ``out_path`` (a ``.png``)."""
        raise NotImplementedError

    def render_batch(self, spots: List[StyledSpot], out_dir: Path) -> List[RenderedImage]:
        """Render every spot in document order into ``out_dir``.

        Shared by all backends. Each image is named by its
        :attr:`StyledSpot.filename` so the export folder lists illustrations in
        article order.
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        rendered: List[RenderedImage] = []
        for spot in spots:
            target = out_dir / spot.filename
            rendered.append(self.render(spot, target))
        return rendered


# --------------------------------------------------------------------------- #
# Mock backend — the keyless, deterministic m2 engine (stdlib-only PNG writer).
# --------------------------------------------------------------------------- #


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """Assemble one PNG chunk (length + tag + data + CRC32)."""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write a valid solid-colour RGB PNG using only the stdlib.

    A faithful, minimal PNG encoder: IHDR + a single IDAT (zlib-compressed raw
    scanlines, each prefixed with filter byte 0) + IEND. This keeps the keyless
    mock free of any image dependency while still producing files that every
    viewer (and the 公众号/小红书 editor) opens as real images.
    """
    r, g, b = rgb
    # Each row: filter byte 0x00, then width * (R,G,B).
    row = b"\x00" + bytes((r, g, b)) * width
    raw = row * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


class MockImageBackend(ImageBackend):
    """Keyless placeholder renderer — writes real PNGs with no API key.

    Strategy (deterministic — same plan always yields the same batch):

    * The canvas matches the pack's aspect ratio (16:9 → 480×270), so the
      placeholders preview at the real shape.
    * The tint is derived from ``seed`` (shared by the whole batch) blended with
      a hash of the spot's depicts phrase. Result: every image in one article
      sits in the *same* hue family (the consistency lock is visible) while each
      spot is individually distinguishable.

    This is enough to *see the whole pipeline* — placement → style-lock →
    render → export — without spending a single credit, which is the entire
    point of the keyless demo and of green CI.
    """

    name = "mock"
    requires_key = False

    # Base canvas; the long edge scales the short edge by the pack aspect.
    _LONG_EDGE = 480

    def render(self, spot: StyledSpot, out_path: Path) -> RenderedImage:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        w, h = self._canvas(spot)
        rgb = self._tint(spot)
        _write_png(out_path, w, h, rgb)
        return RenderedImage(path=out_path, spot=spot)

    def _canvas(self, spot: StyledSpot) -> tuple[int, int]:
        try:
            aw_s, ah_s = spot.aspect_ratio.split(":")
            aw, ah = int(aw_s), int(ah_s)
            if aw <= 0 or ah <= 0:
                raise ValueError
        except (ValueError, AttributeError):
            aw, ah = 16, 9
        width = self._LONG_EDGE
        height = max(1, round(width * ah / aw))
        return width, height

    def _tint(self, spot: StyledSpot) -> tuple[int, int, int]:
        """A near-白底 tint, seeded by the batch seed + the spot's brief.

        Kept very light (channels in 232..255) so the placeholders echo the
        pack's 白底 aesthetic; the shared seed dominates the hue so the batch
        reads as one family.
        """
        digest = hashlib.sha256(
            f"{spot.seed}:{spot.depicts}".encode("utf-8")
        ).digest()
        # seed sets the family hue; the phrase nudges each channel a little.
        seed_bytes = hashlib.sha256(str(spot.seed).encode("utf-8")).digest()
        r = 232 + ((seed_bytes[0] + digest[0]) % 24)
        g = 232 + ((seed_bytes[1] + digest[1]) % 24)
        b = 232 + ((seed_bytes[2] + digest[2]) % 24)
        return min(r, 255), min(g, 255), min(b, 255)


# --------------------------------------------------------------------------- #
# Real adapters — text-to-image over httpx.
# --------------------------------------------------------------------------- #


class _HttpImageBackend(ImageBackend):
    """Shared HTTP plumbing for text-to-image providers.

    Concrete providers set ``name`` and implement :meth:`_request_image`, which
    returns the raw PNG/JPEG bytes for one prompt. The ``httpx`` import is
    deferred to call time so importing the package — and running the keyless
    mock path — never requires it.
    """

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        if not api_key:
            raise ValueError(
                f"{self.name} backend requires an API key — run `duanhui init` "
                "or set the matching env var, or stay in --dry-run / mock mode."
            )
        self._api_key = api_key
        self._timeout = timeout

    def render(self, spot: StyledSpot, out_path: Path) -> RenderedImage:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = self._request_image(spot)
        out_path.write_bytes(data)
        return RenderedImage(path=out_path, spot=spot)

    def _request_image(self, spot: StyledSpot) -> bytes:  # pragma: no cover - network
        raise NotImplementedError

    @staticmethod
    def _aspect_size(spot: StyledSpot, long_edge: int = 1280) -> str:
        """Format the canvas size string (e.g. ``"1280*720"``) for the API."""
        try:
            aw_s, ah_s = spot.aspect_ratio.split(":")
            aw, ah = int(aw_s), int(ah_s)
        except (ValueError, AttributeError):
            aw, ah = 16, 9
        height = max(1, round(long_edge * ah / aw))
        return f"{long_edge}*{height}"


class TongyiWanxiangBackend(_HttpImageBackend):
    """通义万相 (Alibaba DashScope text-to-image).

    DashScope image synthesis runs **asynchronously**: the ``X-DashScope-Async:
    enable`` header makes the POST only *submit* the job and return a
    ``task_id`` (``output.task_status`` is ``PENDING``); the finished image URL
    is not in the POST body and only appears after polling the task endpoint
    until ``task_status == "SUCCEEDED"``. The previous code set the async
    header but read ``output.results`` straight off the POST response — which
    is empty in async mode — so every real keyed run raised "no image url".
    """

    name = "tongyi-wanxiang"
    _ENDPOINT = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/"
        "text2image/image-synthesis"
    )
    _TASK_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"

    # Polling cadence / ceiling for the async task (DashScope image jobs finish
    # in a few seconds). The interval stays inside the request timeout budget.
    _POLL_INTERVAL = 1.0
    _POLL_TIMEOUT = 120.0

    def _request_image(self, spot: StyledSpot) -> bytes:  # pragma: no cover - network
        import httpx

        body = {
            "model": "wanx-v1",
            "input": {"prompt": spot.prompt, "negative_prompt": ""},
            "parameters": {
                "size": self._aspect_size(spot),
                "n": 1,
                "seed": spot.seed,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._ENDPOINT, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            # A sync-style result URL would already be present; otherwise async
            # mode returns a task_id to poll until the image is ready.
            url = _dig(data, "output", "results", 0, "url")
            if not url:
                task_id = _dig(data, "output", "task_id")
                if not task_id:
                    raise RuntimeError(
                        f"{self.name}: no task_id or image url in response"
                    )
                url = self._poll_task(client, task_id, headers)
            return client.get(url).content

    def _poll_task(self, client, task_id: str, headers: dict) -> str:
        """Poll the DashScope task endpoint until the image URL is ready.

        Returns the first ``output.results[0].url`` once the task reports
        ``SUCCEEDED``; raises ``RuntimeError`` on ``FAILED`` or timeout.
        """
        import time

        task_url = self._TASK_ENDPOINT.format(task_id=task_id)
        deadline = time.monotonic() + self._POLL_TIMEOUT
        while True:
            resp = client.get(task_url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            status = _dig(data, "output", "task_status")
            if status == "SUCCEEDED":
                url = _dig(data, "output", "results", 0, "url")
                if not url:
                    raise RuntimeError(
                        f"{self.name}: task succeeded but no image url"
                    )
                return url
            if status == "FAILED":
                raise RuntimeError(f"{self.name}: image task failed")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{self.name}: image task timed out")
            time.sleep(self._POLL_INTERVAL)


class KlingBackend(_HttpImageBackend):
    """可灵 (Kuaishou Kling text-to-image)."""

    name = "kling"
    _ENDPOINT = "https://api.klingai.com/v1/images/generations"

    def _request_image(self, spot: StyledSpot) -> bytes:  # pragma: no cover - network
        import httpx

        body = {
            "prompt": spot.prompt,
            "aspect_ratio": spot.aspect_ratio,
            "n": 1,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._ENDPOINT, json=body, headers=headers)
            resp.raise_for_status()
            url = _dig(resp.json(), "data", 0, "url")
            if not url:
                raise RuntimeError(f"{self.name}: no image url in response")
            return client.get(url).content


class JimengBackend(_HttpImageBackend):
    """即梦 (ByteDance Jimeng text-to-image)."""

    name = "jimeng"
    _ENDPOINT = "https://visual.volcengineapi.com/text2image"

    def _request_image(self, spot: StyledSpot) -> bytes:  # pragma: no cover - network
        import httpx

        body = {"prompt": spot.prompt, "size": self._aspect_size(spot), "seed": spot.seed}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._ENDPOINT, json=body, headers=headers)
            resp.raise_for_status()
            url = _dig(resp.json(), "data", "image_urls", 0)
            if not url:
                raise RuntimeError(f"{self.name}: no image url in response")
            return client.get(url).content


class SeeDreamBackend(_HttpImageBackend):
    """SeeDream (text-to-image; OpenAI-compatible images endpoint)."""

    name = "seedream"
    _ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

    def _request_image(self, spot: StyledSpot) -> bytes:  # pragma: no cover - network
        import base64

        import httpx

        body = {
            "model": "seedream",
            "prompt": spot.prompt,
            "size": self._aspect_size(spot),
            "response_format": "b64_json",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._ENDPOINT, json=body, headers=headers)
            resp.raise_for_status()
            b64 = _dig(resp.json(), "data", 0, "b64_json")
            if b64:
                return base64.b64decode(b64)
            url = _dig(resp.json(), "data", 0, "url")
            if url:
                return client.get(url).content
            raise RuntimeError(f"{self.name}: no image payload in response")


def _dig(data: object, *path: object) -> object:
    """Safely walk a nested dict/list response, returning ``None`` on any miss."""
    cur = data
    for key in path:
        try:
            if isinstance(key, int) and isinstance(cur, (list, tuple)):
                cur = cur[key]
            elif isinstance(cur, dict):
                cur = cur.get(key)  # type: ignore[arg-type]
            else:
                return None
        except (IndexError, KeyError, TypeError):
            return None
        if cur is None:
            return None
    return cur
