# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SuperMarioYL/duanhui/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.2.0
[0.1.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.1.0
