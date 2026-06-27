# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SuperMarioYL/duanhui/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SuperMarioYL/duanhui/releases/tag/v0.1.0
