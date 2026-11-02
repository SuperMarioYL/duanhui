# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-11-03

### Fixed
- **`duanhui.placement` and `duanhui.style` now import cleanly on their own**:
  each module reached the other through `duanhui.backends`, so importing either
  as the first import of a fresh interpreter died with
  `ImportError: cannot import name '…' from partially initialized module …
  (most likely due to a circular import)`. The suite never noticed because
  pytest's first test imports `duanhui.cli`, which warms the module graph. Both
  imports were only used in type annotations (`from __future__ import
  annotations` is already in force), so they now live under `TYPE_CHECKING` and
  the cycle is gone (fix-public-module-import-cycle).
- **Markdown list items are no longer fused into one garbled paragraph**:
  consecutive list items in a blank-line block were re-joined into a single
  `Segment.text` (`第一个要点…第二个要点…第三个要点…` with no separator at all),
  which flowed into the LLM placement payload and into `annotated.md`'s
  reproduction of the article. `_split_paragraphs` now starts a new paragraph at
  every list-item line while indented continuation lines still join their item;
  `1.5倍` / `3.14` and blank-line-separated lists are unchanged
  (fix-list-block-paragraph-merge).
- **cover mode's export report names the file it actually wrote**: the shared
  summary claimed `images/ 1 张 PNG` and told the author to paste PNGs from
  `images/`, but `--cover` writes `cover.png` at the bundle root and leaves
  `images/` empty. The cover branch now reports `cover.png` and the paste
  instruction points at the article cover position (fix-cover-export-summary).
- **an `-o` that names an existing file exits 2 instead of printing a
  traceback**: the `mkdir` deep inside the render/export path surfaced
  `NotADirectoryError` / `FileExistsError` as a raw rich traceback + exit 1. A
  new `_ensure_out_dir` guard creates the export folder up front and prints the
  same clean red message + exit 2 the CLI already uses for a bad article path
  (fix-out-dir-traceback).

### Changed
- **one derived version-lockstep guard replaces four hardcoded copies**: the
  duplicated version tests in `tests/test_v030.py` … `tests/test_v060.py` were
  replaced by `tests/test_version_lockstep.py`, which derives the expected
  version from `VERSION` and asserts `__version__`, the CLI, `pyproject.toml`,
  both README locales, all three `web/site.json` version strings,
  `docs/demo-results.json`, and the CHANGELOG head + link references agree.
  `docs/demo-results.json` had been stuck at `0.5.0`, the CHANGELOG had no link
  refs for 0.4.0–0.6.0, and two user-visible CLI help strings were stale
  (`list-styles` said "v0.2.0"; `--max-spots` claimed a fixed 6–8 default) — all
  corrected (fix-version-and-doc-surface-lockstep).

## [0.6.0] - 2026-09-23

### Fixed
- **GBK/GB18030-encoded articles no longer crash `duanhui run` with a raw
  traceback**: `_read_article` read the article as UTF-8 and guarded only
  `OSError`, but a GBK file (the default Windows-Notepad/WPS encoding for
  Simplified Chinese) raises `UnicodeDecodeError` — a `ValueError`, not an
  `OSError` — so the guard missed it and the run died inside a multi-line
  traceback. `_read_article` now retries once with the `gb18030` superset
  codec (so the common Windows-Notepad case just works) and falls back to a
  clean red message + exit 2 when the file is neither UTF-8 nor GB18030
  (fix-article-decode-gbk-traceback).
- **segmentation no longer leaks Markdown markers into `Segment.text`**:
  blockquote lines kept their `> ` prefix, spaceless CJK ordered-list markers
  (`1.要点` / `1、要点`) kept their numbers, and a second heading inside one
  blank-line block (`# A` + `## B` + body) merged into its body instead of
  becoming its own transition-tagged paragraph. `_strip_markdown` now strips
  leading blockquote markers, the list-marker rule accepts the spaceless CJK
  form with a `(?!\d)` guard so decimals like `1.5倍` stay intact, and
  `_split_paragraphs` splits every heading-only line onto its own paragraph
  (fix-segment-markdown-cleanup-gaps).
- **hard-wrapped Chinese paragraphs no longer grow a spurious half-width
  space at every line wrap**: `_split_paragraphs` re-joined a paragraph's
  soft-wrapped lines with `" ".join`, inserting a mid-sentence space at every
  wrap boundary of CJK text (the drift flowed into the LLM payload and into
  `annotated.md`). The join is now CJK-aware: no separator when either
  boundary character is CJK, a single space between two non-CJK words so
  hard-wrapped English still reads correctly
  (fix-cjk-hardwrap-join-space).

## [0.5.0] - 2026-09-07

### Fixed
- **keyed runs no longer crash with an opaque traceback when a backend POST
  fails**: both backend families validated every POST with
  `raise_for_status()`, but the `httpx.HTTPStatusError` it raises on a 4xx/5xx
  (a 401 expired key, 429 rate limit, or 500 provider outage) is not a
  `RuntimeError`, so it escaped the CLI's `except RuntimeError` guards around
  placement and render and surfaced as a raw traceback + exit 1. The v0.4.0 fix
  had already wrapped the image DOWNLOAD (GET) path in a clear `RuntimeError`
  via `_fetch_image_bytes`; the POST path was left unwrapped — an asymmetric
  gap. The LLM placement backend's POST and all four real image backends' POSTs
  (plus the tongyi task-poll GET) now route through a shared `_post` /
  `_post_json` helper that catches `httpx.HTTPError` and re-raises a clear
  `RuntimeError`, so a failed request prints a red message and exits cleanly
  instead of a traceback. Since deepseek is the default LLM backend and
  tongyi-wanxiang the default image backend, any keyed user whose provider
  returns a non-2xx hit this on their first real run.

## [0.4.0] - 2026-08-28

### Fixed
- **keyed LLM run no longer crashes on a non-standard chat response**: the
  OpenAI-compatible placement backend read the completion as
  `data["choices"][0]["message"]["content"]`, which raised an opaque
  KeyError / IndexError / AttributeError when a CN provider returned a
  200-with-error-body (no `choices`), an empty `choices` list, or a `null`
  `content` (refusal/filtered turn). The content is now extracted safely
  (returning `None` on any miss, mirroring the image backends' `_dig`), and a
  missing/`None`/non-string content or an unparseable JSON response surfaces as
  a clear `RuntimeError` that `duanhui run` prints and exits on instead of a
  traceback.
- **real image backends no longer write a corrupt PNG on a failed download**:
  all four real image backends fetched the finished image via
  `client.get(url).content` with no `raise_for_status`, so a failed/expired
  download (a short-lived presigned URL from the default tongyi-wanxiang, or a
  403/404/5xx) returned an error body that was written verbatim as a `.png`. A
  shared `_fetch_image_bytes` helper now validates the download and surfaces any
  failure as a clear `RuntimeError` instead of a corrupt file.
- **real image backends no longer crash on a zero-dimension aspect ratio**: a
  hand-edited style pack with a zero-dimension `aspect_ratio` (e.g. `"0:9"`)
  made `_aspect_size` divide by zero, crashing the real backends with an
  uncaught `ZeroDivisionError`. The mock's zero-guard is now mirrored in
  `_aspect_size` so a bad ratio falls back to 16:9 instead of crashing.

## [0.3.0] - 2026-08-22

### Fixed
- **通义万相 default image backend now polls for the result**: the async
  (`X-DashScope-Async: enable`) POST only submits the job and returns a
  `task_id`, but the backend read `output.results` straight off the POST
  response — which is empty in async mode — so every real keyed run raised
  `RuntimeError("tongyi-wanxiang: no image url in response")`. The backend now
  reads `output.task_id` and polls the DashScope task endpoint until
  `task_status == "SUCCEEDED"` before fetching the image URL.
- **an invalid LLM backend name no longer forces the image backend to mock**:
  a typo'd `llm_backend` in config used to set the global `mock` flag in
  `Config.load`, which silently degraded a valid, keyed image backend to the
  keyless mock (cross-family contamination). The block is removed; each family
  now degrades independently via its own validity guard in
  `effective_*_backend()`.
- **out-of-range `max_spots` in the config file no longer crashes `run`**: a
  hand-edited `max_spots: 15` (or `0`) raised an uncaught pydantic
  `ValidationError` because the file path was not clamped like the CLI
  `--max-spots` (`min=1, max=12`). `Config.load` now clamps the value into
  `[1, 12]`. New `MAX_SPOTS_MIN` / `MAX_SPOTS_MAX` constants are the single
  source of truth shared by the `Field` constraint and the clamp.

## [0.2.0] - 2026-08-02

### Fixed
- **m4 — unknown image-backend name no longer crashes `run`**: a typo'd /
  unknown `image_backend` name in config with a key present used to make
  `effective_image_backend()` return the bogus name and `make_image_backend()`
  raise `KeyError` at render time, while the LLM side degraded to mock. The
  image family now validates its configured name and degrades to the keyless
  `MockImageBackend` (matching the LLM side); the validity guard also moved
  into `effective_llm_backend()` / `effective_image_backend()` so a
  directly-constructed `Config` is protected on both sides, not only the
  `load()` path. New `is_valid_image_backend()` helper.

### Added
- **m5 — cover mode (`duanhui run --cover`)**: the base plan's v0.2 hedge.
  Derives ONE hero cover illustration from the article title + opening semantics
  (reusing the placement + style-lock primitives, no new model), renders a single
  `cover.png`, and exports a cover `placement_map.json` + `annotated.md`. A
  direct de-risk of the kill-criteria §8 demand-shape falsifier (authors who
  want 封面/单图). Additive — `duanhui run` without `--cover` is unchanged.
- **m6 — runtime style pack selection**: ships a second locked style pack
  `styles/shuimo.yaml` (水墨 ink-wash: near-white 淡墨 background, black brush
  lines, 朱砂红 accents, 16:9, distinct `consistency_seed`). The already-pluggable
  `StyleLock` loader is now activated via `duanhui run --style <pack_id>` and a
  new `duanhui list-styles` command. Each run still locks its batch to one hand;
  only WHICH pack is locked becomes selectable.
- New regression suite `tests/test_v020.py` (20 tests; m4/m5/m6). Full suite now
  67 tests, all keyless / CI-green.

## [0.1.0] - 2026-06-28

### Added
- **m1 — segment & place**: paste a Chinese article and print a deterministic
  `PlacementPlan` (which paragraphs get an illustration and what each one
  depicts), splitting the article into role-tagged paragraph segments and
  running entirely on `MockLLMBackend` with **no API keys**.
- **m2 — style lock & render**: turn each planned spot into a style-locked
  怪诞手绘 prompt (白底 + 红橙蓝批注 + 16:9 + shared consistency seed from
  `styles/guaidan.yaml`) and render a consistent batch through a pluggable
  image backend, with `MockImageBackend` producing keyless placeholder PNGs in CI.
- **m3 — export bundle**: assemble images + `placement_map.json`
  (spot → file → segment index) + an annotated markdown into one drop-in
  export folder for 公众号 / 小红书.
- Pluggable backend registry for both LLM (DeepSeek / Qwen / GLM) and image
  (通义万相 / 可灵 / 即梦 / SeeDream) families, all behind a dry-run / mock toggle.
- `duanhui` CLI with `run`, `init`, and `--dry-run`, a keyless end-to-end test
  suite, and a bilingual (简体中文 / English) README.

[Unreleased]: https://github.com/SuperMarioYL/duanhui/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.7.0
[0.6.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.6.0
[0.5.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.5.0
[0.4.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.4.0
[0.3.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.3.0
[0.2.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.1.0
