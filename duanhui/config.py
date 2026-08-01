"""Configuration — backend selection, API keys, and the mock/dry-run toggle.

DuanHui runs end-to-end with **zero keys** by defaulting every backend to its
mock. Real backends are opt-in: a user either runs ``duanhui init`` (which
writes ``~/.duanhui/config.yaml``) or sets the matching environment variables.

Resolution order for any setting (highest priority first):

1. an explicit override passed in code / by a CLI flag,
2. an environment variable,
3. the YAML config file,
4. the keyless default (mock).

This module is deliberately dependency-light: it reads YAML via ``pyyaml`` and
exposes a single :class:`Config` object that the CLI and pipeline consume.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, Field

from duanhui.backends import (
    available_image_backends,
    available_llm_backends,
    is_mock_image,
    is_mock_llm,
)

__all__ = [
    "Config",
    "default_config_path",
    "DEFAULT_LLM_BACKEND",
    "DEFAULT_IMAGE_BACKEND",
    "ENV_LLM_KEYS",
    "ENV_IMAGE_KEYS",
    "is_valid_backend",
    "is_valid_image_backend",
]

# The plan's default backends (model_targets in the frontmatter).
DEFAULT_LLM_BACKEND = "deepseek"
DEFAULT_IMAGE_BACKEND = "tongyi-wanxiang"

# Image backends the config knows about (rendering itself lands in m2).
KNOWN_IMAGE_BACKENDS = ["tongyi-wanxiang", "kling", "jimeng", "seedream"]

# Per-backend environment variable that supplies the API key.
ENV_LLM_KEYS: Dict[str, str] = {
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
}
ENV_IMAGE_KEYS: Dict[str, str] = {
    "tongyi-wanxiang": "DASHSCOPE_API_KEY",
    "kling": "KLING_API_KEY",
    "jimeng": "JIMENG_API_KEY",
    "seedream": "SEEDREAM_API_KEY",
}

# Env var that forces mock mode regardless of configured keys (used by CI).
ENV_FORCE_MOCK = "DUANHUI_MOCK"


def default_config_path() -> Path:
    """Location of the user config file (``~/.duanhui/config.yaml``)."""
    base = os.environ.get("DUANHUI_HOME")
    root = Path(base) if base else Path.home() / ".duanhui"
    return root / "config.yaml"


class Config(BaseModel):
    """Resolved DuanHui configuration.

    A ``Config`` is normally built with :meth:`load`, which layers env vars over
    the YAML file over the keyless defaults. Tests and the dry-run path can also
    construct one directly with ``Config(llm_backend="mock")``.
    """

    llm_backend: str = Field(default=DEFAULT_LLM_BACKEND)
    image_backend: str = Field(default=DEFAULT_IMAGE_BACKEND)
    llm_api_key: Optional[str] = Field(default=None, repr=False)
    image_api_key: Optional[str] = Field(default=None, repr=False)
    max_spots: Optional[int] = Field(default=None, ge=1, le=12)
    # When True, every backend is forced to its keyless mock.
    mock: bool = Field(default=False)

    model_config = {"validate_assignment": True}

    # --- derived helpers ----------------------------------------------------

    def effective_llm_backend(self) -> str:
        """The LLM backend name actually used, honouring the mock toggle.

        Falls back to ``"mock"`` whenever mock mode is on, the configured name is
        itself the mock, no key is available, **or the configured name is not a
        known backend** (a typo degrades to the keyless preview instead of
        crashing at ``make_llm_backend``). The same validity guard the image
        family uses — the two backend families are symmetric.
        """
        if self.mock or is_mock_llm(self.llm_backend):
            return "mock"
        if not self.llm_api_key or not is_valid_backend(self.llm_backend):
            return "mock"
        return self.llm_backend

    def uses_mock_llm(self) -> bool:
        return self.effective_llm_backend() == "mock"

    def effective_image_backend(self) -> str:
        """The image backend name actually used, honouring the mock toggle.

        Same degradation rule as the LLM side: mock mode, a mock selection, a
        real backend with no key, **or an unknown/typo'd backend name** all fall
        back to the keyless :class:`~duanhui.backends.image.MockImageBackend`, so a
        full ``run`` always produces a batch of PNGs even with zero keys — and a
        misconfigured image backend never crashes the render step (the v0.1.0
        image family lacked the LLM side's name-validity guard; v0.2.0 closes
        that asymmetry so both families degrade symmetrically).
        """
        if self.mock or is_mock_image(self.image_backend):
            return "mock"
        if not self.image_api_key or not is_valid_image_backend(self.image_backend):
            return "mock"
        return self.image_backend

    def uses_mock_image(self) -> bool:
        return self.effective_image_backend() == "mock"

    # --- loading ------------------------------------------------------------

    @classmethod
    def load(
        cls,
        path: Optional[Path] = None,
        *,
        force_mock: Optional[bool] = None,
    ) -> "Config":
        """Build a config from YAML + environment, layered over defaults.

        Parameters
        ----------
        path:
            Config file to read. Defaults to :func:`default_config_path`. A
            missing file is fine — the keyless defaults are used.
        force_mock:
            If given, overrides every key source and pins mock mode. The CLI
            sets this for ``--dry-run``; CI sets ``DUANHUI_MOCK=1``.
        """
        path = path or default_config_path()
        file_data = _read_yaml(path)

        llm_backend = str(file_data.get("llm_backend") or DEFAULT_LLM_BACKEND).strip()
        image_backend = str(
            file_data.get("image_backend") or DEFAULT_IMAGE_BACKEND
        ).strip()

        # Keys: env var wins over the (optional) inline key in the YAML file.
        llm_key = _resolve_key(
            env_name=ENV_LLM_KEYS.get(llm_backend),
            file_value=_nested(file_data, "keys", "llm"),
        )
        image_key = _resolve_key(
            env_name=ENV_IMAGE_KEYS.get(image_backend),
            file_value=_nested(file_data, "keys", "image"),
        )

        max_spots = file_data.get("max_spots")
        try:
            max_spots = int(max_spots) if max_spots is not None else None
        except (TypeError, ValueError):
            max_spots = None

        env_force = os.environ.get(ENV_FORCE_MOCK, "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        mock = bool(force_mock) if force_mock is not None else env_force
        if not is_valid_backend(llm_backend):
            # an unknown configured backend silently degrades to mock.
            mock = True

        return cls(
            llm_backend=llm_backend,
            image_backend=image_backend,
            llm_api_key=llm_key,
            image_api_key=image_key,
            max_spots=max_spots,
            mock=mock,
        )

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist the non-secret parts of this config to YAML.

        Keys are written under ``keys:`` so a user *can* store them inline, but
        ``duanhui init`` prefers env vars; this method exists so the CLI can
        round-trip backend choices.
        """
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {
            "llm_backend": self.llm_backend,
            "image_backend": self.image_backend,
        }
        if self.max_spots is not None:
            data["max_spots"] = self.max_spots
        keys: Dict[str, str] = {}
        if self.llm_api_key:
            keys["llm"] = self.llm_api_key
        if self.image_api_key:
            keys["image"] = self.image_api_key
        if keys:
            data["keys"] = keys
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def is_valid_backend(name: str) -> bool:
    """Whether ``name`` is a known LLM backend name (including ``mock``)."""
    return name.strip().lower() in set(available_llm_backends())


def is_valid_image_backend(name: str) -> bool:
    """Whether ``name`` is a known image backend name (including ``mock``).

    Mirrors :func:`is_valid_backend` for the image family so an unknown/typo'd
    image-backend name degrades to the keyless mock instead of crashing
    ``make_image_backend`` — closing the v0.1.0 asymmetry where only the LLM
    side validated its configured name.
    """
    return name.strip().lower() in set(available_image_backends())


def _read_yaml(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _nested(data: Dict[str, Any], *keys: str) -> Optional[str]:
    cur: Any = data
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur if isinstance(cur, str) and cur.strip() else None


def _resolve_key(*, env_name: Optional[str], file_value: Optional[str]) -> Optional[str]:
    if env_name:
        env_val = os.environ.get(env_name)
        if env_val and env_val.strip():
            return env_val.strip()
    return file_value
