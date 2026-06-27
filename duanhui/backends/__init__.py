"""Pluggable backend registry for DuanHui.

Two backend families live here, each a small adapter interface with a Mock
implementation that needs no API key:

- ``llm``   — placement-planning models (DeepSeek / Qwen / GLM + MockLLMBackend)
- ``image`` — image-generation models (通义万相 / 可灵 / 即梦 / SeeDream + MockImageBackend)

The registry lets :mod:`duanhui.config` select a concrete backend by name while
the rest of the pipeline depends only on the abstract interfaces. Real adapters
take an API key in their constructor; mock backends take none, so the whole
pipeline is constructible — and observable — with **zero keys**.
"""

from __future__ import annotations

from typing import Dict, Type

from duanhui.backends.image import (
    ImageBackend,
    JimengBackend,
    KlingBackend,
    MockImageBackend,
    SeeDreamBackend,
    TongyiWanxiangBackend,
)
from duanhui.backends.llm import (
    DeepSeekBackend,
    GLMBackend,
    LLMBackend,
    MockLLMBackend,
    QwenBackend,
)

__all__ = [
    # LLM (placement) family
    "LLMBackend",
    "MockLLMBackend",
    "LLM_BACKENDS",
    "MOCK_LLM_NAME",
    "available_llm_backends",
    "make_llm_backend",
    "is_mock_llm",
    # image (render) family
    "ImageBackend",
    "MockImageBackend",
    "IMAGE_BACKENDS",
    "MOCK_IMAGE_NAME",
    "available_image_backends",
    "make_image_backend",
    "is_mock_image",
]

#: registry-name → class, for the keyed (real) LLM placement backends.
LLM_BACKENDS: Dict[str, Type[LLMBackend]] = {
    DeepSeekBackend.name: DeepSeekBackend,
    QwenBackend.name: QwenBackend,
    GLMBackend.name: GLMBackend,
}

#: registry-name → class, for the keyed (real) image-generation backends.
IMAGE_BACKENDS: Dict[str, Type[ImageBackend]] = {
    TongyiWanxiangBackend.name: TongyiWanxiangBackend,
    KlingBackend.name: KlingBackend,
    JimengBackend.name: JimengBackend,
    SeeDreamBackend.name: SeeDreamBackend,
}

#: the keyless mock backend's registry name.
MOCK_LLM_NAME = MockLLMBackend.name  # "mock"
MOCK_IMAGE_NAME = MockImageBackend.name  # "mock"


def available_llm_backends() -> list[str]:
    """All selectable LLM backend names, mock first."""
    return [MOCK_LLM_NAME, *LLM_BACKENDS.keys()]


def is_mock_llm(name: str) -> bool:
    """Whether ``name`` selects the keyless mock LLM backend."""
    return name.strip().lower() in {MOCK_LLM_NAME, "", "none", "off"}


def make_llm_backend(name: str, *, api_key: str | None = None) -> LLMBackend:
    """Instantiate an LLM placement backend by registry name.

    Parameters
    ----------
    name:
        One of :func:`available_llm_backends`. ``"mock"`` (or empty / ``none``)
        selects the keyless :class:`MockLLMBackend`.
    api_key:
        Required for the real adapters; ignored by the mock.

    Raises
    ------
    KeyError
        If ``name`` is not a known backend.
    ValueError
        If a real backend is selected without an API key.
    """
    key = name.strip().lower()
    if is_mock_llm(key):
        return MockLLMBackend()
    if key not in LLM_BACKENDS:
        raise KeyError(
            f"unknown LLM backend {name!r}; choose one of {available_llm_backends()}"
        )
    return LLM_BACKENDS[key](api_key=api_key or "")


# --------------------------------------------------------------------------- #
# image (render) family
# --------------------------------------------------------------------------- #


def available_image_backends() -> list[str]:
    """All selectable image backend names, mock first."""
    return [MOCK_IMAGE_NAME, *IMAGE_BACKENDS.keys()]


def is_mock_image(name: str) -> bool:
    """Whether ``name`` selects the keyless mock image backend."""
    return name.strip().lower() in {MOCK_IMAGE_NAME, "", "none", "off"}


def make_image_backend(name: str, *, api_key: str | None = None) -> ImageBackend:
    """Instantiate an image-generation backend by registry name.

    Parameters
    ----------
    name:
        One of :func:`available_image_backends`. ``"mock"`` (or empty / ``none``)
        selects the keyless :class:`MockImageBackend`.
    api_key:
        Required for the real adapters; ignored by the mock.

    Raises
    ------
    KeyError
        If ``name`` is not a known backend.
    ValueError
        If a real backend is selected without an API key.
    """
    key = name.strip().lower()
    if is_mock_image(key):
        return MockImageBackend()
    if key not in IMAGE_BACKENDS:
        raise KeyError(
            f"unknown image backend {name!r}; "
            f"choose one of {available_image_backends()}"
        )
    return IMAGE_BACKENDS[key](api_key=api_key or "")
