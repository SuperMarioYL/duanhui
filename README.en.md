**English** | [简体中文](README.md)

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="1000" alt="Segment a Chinese article, preview illustration positions and briefs, then render with a selected backend and export placement metadata.">
</picture>

**Segment a Chinese article, preview illustration positions and briefs, then render with a selected backend and export placement metadata.**

`v0.6.0` · `Python 3.12+` · [Apache-2.0](LICENSE)

[Website](https://duanhui.lei6393.com) · [Demo record](docs/demo-results.json)

## Why use it

A multi-image article needs both visual direction and a decision about where each image belongs. DuanHui connects segmentation, placement planning and shared style prompts so authors can review the plan before a potentially paid rendering step.

## Architecture

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="1000" alt="segment.py splits and tags paragraphs. placement.py validates returned positions and briefs; style.py adds shared preambles and seeds. The image backend renders, and export.py writes images, placement_map.json and annotated.md. cover.py implements the single-cover route.">
</picture>

segment.py splits and tags paragraphs. placement.py validates returned positions and briefs; style.py adds shared preambles and seeds. The image backend renders, and export.py writes images, placement_map.json and annotated.md. cover.py implements the single-cover route.

Current styles include [guaidan](styles/guaidan.yaml) and [shuimo](styles/shuimo.yaml), selected with --style. The implementation also includes --cover mode.

## Install

Requires Python 3.12+. --dry-run forces mock behavior; the examples also set DUANHUI_MOCK=1 to avoid configured backend calls.

```bash
git clone https://github.com/SuperMarioYL/duanhui.git
cd duanhui
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Quickstart

The actual article and cover dry-runs split the sample into thirteen segments, choose up to three illustration spots and create one cover plan. They do not render real illustrations or validate model aesthetics or consistency.

```bash
DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --dry-run -n 3
DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --cover --dry-run
```

The complete article is [examples/sample_article.md](examples/sample_article.md), with commands in the [replay script](examples/presentation_demo.sh).

## Usage

run FILE --dry-run previews the plan, -n caps placements, --cover selects the cover path and --style chooses a style. Removing --dry-run enables rendering and export: keyless runs use mock placeholders, while valid credentials may enable paid APIs.

## Recorded demo

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="1000" alt="The actual article and cover dry-runs split the sample into thirteen segments, choose up to three illustration spots and create one cover plan. They do not render real illustrations or validate model aesthetics or consistency.">
</picture>

### Article placement plan

The thirteen-segment article yields three mock spots.

```text
$ DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --dry-run -n 3
✓ 分段完成：13 段（标题：为什么你的待办清单总是越列越长？）
                          配图计划 · 共 3 处（mock 预览，未消耗任何额度）
┏━━━┳━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ # ┃ 段落 ┃ 角色    ┃ 配什么（depicts）                    ┃ 为什么                               ┃
┡━━━╇━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 1 │   §1 │ concept │ 列下一长串今天要做的事               │ 核心概念段落，配图帮助读者把抽象点 … │
│ 2 │   §3 │ concept │ 帕金森定律指的是这样一个现象：一项 … │ 核心概念段落，配图帮助读者把抽象点 … │
│ 3 │   §6 │ concept │ 清单本身也有成本                     │ 核心概念段落，配图帮助读者把抽象点 … │
└───┴──────┴─────────┴──────────────────────────────────────┴──────────────────────────────────────┘
╭──────────────────────────────────────────── dry-run ─────────────────────────────────────────────╮
│ 这是 --dry-run 预览，未渲染任何图片，也没有消耗额度。                                            │
│ 去掉 --dry-run 即可渲染并导出整套配图；运行 duanhui init 接入真实的国产图像后端。                │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### Cover plan

Create a single title-derived cover brief without rendering.

```text
$ DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --cover --dry-run
✓ 分段完成：13 段（标题：为什么你的待办清单总是越列越长？）
✓ 封面计划：为什么你的待办清单总是越列越长 （mock 预览未消耗额度）
╭─────────────────────────────────────── dry-run · 封面模式 ───────────────────────────────────────╮
│ 这是 --dry-run 预览，未渲染任何图片，也没有消耗额度。                                            │
│ 去掉 --dry-run 即可渲染封面图（cover.png）；运行 duanhui init 接入真实的国产图像后端。           │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

## Capabilities and integration

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="1000" alt="placement_map records paragraph indices, prompts and seeds, while annotated.md guides placement. Shared seeds and preambles control inputs but do not by themselves guarantee a uniform visual style across model outputs.">
</picture>

placement_map records paragraph indices, prompts and seeds, while annotated.md guides placement. Shared seeds and preambles control inputs but do not by themselves guarantee a uniform visual style across model outputs.



## Configuration

Settings resolve from CLI overrides, environment, YAML and defaults. init writes ~/.duanhui/config.yaml; DUANHUI_HOME changes the directory and DUANHUI_MOCK=1 forces mocks. LLM routes include deepseek/qwen/glm, and image routes include Tongyi Wanxiang, Kling, Jimeng and Seedream, using their configured credentials.

## Roadmap and scope

v0.6.0 includes article planning, cover mode, two style packs, rendering adapters and export bundles. More styles and hosting remain future directions; no live cloud subscription or guaranteed price is offered.

- Mock plans and placeholders do not establish real-model semantic or image quality.
- Shared style prompts do not guarantee identical visual consistency across generated images.
- Remote APIs, image rendering and platform publishing were not exercised in this demo.

[Terminal recording](assets/demo.gif) · [Recording script](docs/demo.tape)

## License

[Apache-2.0](LICENSE)
