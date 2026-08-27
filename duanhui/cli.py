"""``duanhui`` — the command-line entry point.

Subcommands
-----------
* ``duanhui run ARTICLE.md [--dry-run] [-o out/]`` — the whole pipeline. With
  ``--dry-run`` it stops after printing the placement plan (keyless preview); a
  full run continues through style-lock → render → export and writes a drop-in
  bundle.
* ``duanhui init`` — interactively writes ``~/.duanhui/config.yaml`` (pick the
  LLM + image backends, paste keys). Skippable — you can stay in mock forever.
* ``duanhui backends`` — list the selectable LLM / image backends.

Every command runs **keyless by default**: with no config and no keys, ``run``
degrades to the mock backends and still produces a full export folder, so a
first-time user sees the entire value before spending a credit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from duanhui import __version__
from duanhui.backends import (
    available_image_backends,
    available_llm_backends,
    make_image_backend,
    make_llm_backend,
)
from duanhui.config import (
    DEFAULT_IMAGE_BACKEND,
    DEFAULT_LLM_BACKEND,
    Config,
    default_config_path,
)
from duanhui.cover import (
    COVER_FILENAME,
    build_cover_styled_spot,
    export_cover,
    plan_cover,
    render_cover,
)
from duanhui.export import export_bundle
from duanhui.placement import PlacementPlan, plan_article
from duanhui.segment import Segment, segment_article
from duanhui.style import (
    DEFAULT_PACK_ID,
    available_style_packs,
    build_styled_spots,
    load_style_pack,
)

app = typer.Typer(
    name="duanhui",
    help="段绘 — 把整篇中文文章按段落语义批量配上同风格白底怪诞手绘插图。",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


@app.callback()
def _main() -> None:
    """段绘（DuanHui）命令行。运行 `duanhui version` 查看版本。"""


def _read_article(path: Path) -> str:
    if not path.exists():
        console.print(f"[red]找不到文件：{path}[/red]")
        raise typer.Exit(code=2)
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - fs error
        console.print(f"[red]读取失败：{exc}[/red]")
        raise typer.Exit(code=2)


def _derive_title(text: str, path: Path) -> str:
    """Use the first Markdown H1 as the title, else the file stem."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return path.stem


def _render_plan_table(
    plan: PlacementPlan, segments: list[Segment], *, mock: bool
) -> None:
    """Pretty-print the placement plan so the author sees the decisions."""
    seg_by_idx = {s.idx: s for s in segments}
    table = Table(
        title=f"配图计划 · 共 {plan.count} 处" + ("（mock 预览，未消耗任何额度）" if mock else ""),
        show_lines=False,
        header_style="bold",
    )
    table.add_column("#", justify="right", style="cyan", no_wrap=True)
    table.add_column("段落", justify="right", style="dim", no_wrap=True)
    table.add_column("角色", no_wrap=True)
    table.add_column("配什么（depicts）", style="bold")
    table.add_column("为什么", style="dim")

    for i, spot in enumerate(plan.spots, start=1):
        seg = seg_by_idx.get(spot.after_segment_idx)
        role = seg.role.value if seg else "—"
        table.add_row(
            str(i),
            f"§{spot.after_segment_idx}",
            role,
            spot.depicts,
            spot.reason,
        )
    console.print(table)


def _build_config(dry_run: bool) -> Config:
    """Load config; ``--dry-run`` forces the keyless mock backends."""
    return Config.load(force_mock=True if dry_run else None)


def _load_style_or_exit(pack_id: str):
    """Load a shipped style pack by id, or exit with a clear error."""
    packs = available_style_packs()
    try:
        return load_style_pack(pack_id=pack_id)
    except FileNotFoundError:
        console.print(
            f"[red]找不到风格包：{pack_id}[/red]（可选：{', '.join(packs) or '无'}）"
        )
        raise typer.Exit(code=2)
    except ValueError as exc:
        console.print(f"[red]风格包 {pack_id} 无效：{exc}[/red]")
        raise typer.Exit(code=2)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


@app.command()
def run(
    article: Path = typer.Argument(
        ...,
        help="要配图的中文文章（Markdown / 纯文本）。",
        exists=False,
    ),
    out: Optional[Path] = typer.Option(
        None,
        "--out",
        "-o",
        help="导出目录；不传则默认 ./out。--dry-run 时忽略。",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="只打印配图计划，不渲染、不导出，全程 keyless。",
    ),
    max_spots: Optional[int] = typer.Option(
        None,
        "--max-spots",
        "-n",
        min=1,
        max=12,
        help="最多配几张图（默认按文章长度自动估算 6–8 张）。",
    ),
    cover: bool = typer.Option(
        False,
        "--cover",
        help="封面模式：只生成一张封面/主视觉图，不做整篇配图。",
    ),
    style: str = typer.Option(
        DEFAULT_PACK_ID,
        "--style",
        help="风格包 ID（默认 guaidan 怪诞手绘；duanhui list-styles 查看全部）。",
    ),
) -> None:
    """对一篇文章跑完整流水线：分段 → 配图计划 →（渲染 → 导出）。"""
    cfg = _build_config(dry_run)
    if max_spots is not None:
        cfg.max_spots = max_spots

    text = _read_article(article)
    title = _derive_title(text, article)

    # 1) segment ------------------------------------------------------------
    try:
        segments = segment_article(text)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(
        f"[green]✓[/green] 分段完成：{len(segments)} 段（标题：[bold]{title}[/bold]）"
    )

    # 2) placement ----------------------------------------------------------
    llm = make_llm_backend(cfg.effective_llm_backend(), api_key=cfg.llm_api_key)

    # --cover: narrow the placement to a single hero cover illustration.
    if cover:
        cover_plan = plan_cover(segments, article_title=title)
        console.print(
            f"[green]✓[/green] 封面计划：[bold]{cover_plan.depicts}[/bold] "
            f"（mock 预览未消耗额度）" if cfg.uses_mock_llm() else
            f"[green]✓[/green] 封面计划：[bold]{cover_plan.depicts}[/bold]"
        )
        if dry_run:
            console.print(
                Panel(
                    "这是 [bold]--dry-run[/bold] 预览，未渲染任何图片，也没有消耗额度。\n"
                    f"去掉 --dry-run 即可渲染封面图（[bold]{COVER_FILENAME}[/bold]）；"
                    "运行 [bold]duanhui init[/bold] 接入真实的国产图像后端。",
                    title="dry-run · 封面模式",
                    border_style="cyan",
                )
            )
            raise typer.Exit(code=0)

        style_pack = _load_style_or_exit(style)
        styled = build_cover_styled_spot(cover_plan, style_pack)
        out_dir = (out or Path("out")).resolve()
        image_backend = make_image_backend(
            cfg.effective_image_backend(), api_key=cfg.image_api_key
        )
        backend_label = (
            "mock（keyless 占位图）"
            if cfg.uses_mock_image()
            else cfg.effective_image_backend()
        )
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task(
                f"用 {backend_label} 渲染 1 张封面主视觉…", total=None
            )
            try:
                rendered = render_cover(styled, image_backend, out_dir)
            except RuntimeError as exc:
                console.print(f"[red]渲染失败：{exc}[/red]")
                raise typer.Exit(code=1)
        bundle = export_cover(
            styled, rendered, style_pack, out_dir, article_title=title
        )
        console.print(
            f"[green]✓[/green] 已渲染封面图 [bold]{COVER_FILENAME}[/bold]，"
            f"风格包 [bold]{style_pack.pack_id}[/bold]，"
            f"consistency_seed={style_pack.consistency_seed}"
        )
        _render_export_summary(bundle, mock_image=cfg.uses_mock_image())
        return

    try:
        plan = plan_article(
            segments, llm, max_spots=cfg.max_spots, article_title=title
        )
    except RuntimeError as exc:
        console.print(f"[red]配图失败：{exc}[/red]")
        raise typer.Exit(code=1)
    if plan.is_empty():
        console.print("[yellow]没有找到适合配图的段落。[/yellow]")
        raise typer.Exit(code=0)
    _render_plan_table(plan, segments, mock=cfg.uses_mock_llm())

    if dry_run:
        console.print(
            Panel(
                "这是 [bold]--dry-run[/bold] 预览，未渲染任何图片，也没有消耗额度。\n"
                "去掉 --dry-run 即可渲染并导出整套配图；"
                "运行 [bold]duanhui init[/bold] 接入真实的国产图像后端。",
                title="dry-run",
                border_style="cyan",
            )
        )
        raise typer.Exit(code=0)

    # 3) style-lock ---------------------------------------------------------
    style_pack = _load_style_or_exit(style)
    styled = build_styled_spots(plan, style_pack)

    # 4) render -------------------------------------------------------------
    out_dir = (out or Path("out")).resolve()
    images_dir = out_dir / "images"
    image_backend = make_image_backend(
        cfg.effective_image_backend(), api_key=cfg.image_api_key
    )
    backend_label = (
        f"mock（keyless 占位图）" if cfg.uses_mock_image() else cfg.effective_image_backend()
    )
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(
            f"用 {backend_label} 渲染 {len(styled)} 张同风格插图…", total=None
        )
        try:
            rendered = image_backend.render_batch(styled, images_dir)
        except RuntimeError as exc:
            console.print(f"[red]渲染失败：{exc}[/red]")
            raise typer.Exit(code=1)

    # 5) export -------------------------------------------------------------
    bundle = export_bundle(
        segments, styled, rendered, style_pack, out_dir, article_title=title
    )

    console.print(
        f"[green]✓[/green] 已渲染 {bundle.image_count} 张插图，"
        f"风格包 [bold]{style_pack.pack_id}[/bold]，consistency_seed={style_pack.consistency_seed}"
    )
    _render_export_summary(bundle, mock_image=cfg.uses_mock_image())


def _render_export_summary(bundle, *, mock_image: bool) -> None:
    body = (
        f"导出目录：[bold]{bundle.out_dir}[/bold]\n"
        f"  • images/                {bundle.image_count} 张 PNG\n"
        f"  • placement_map.json     图 → 文件 → 段落 的机器可读映射\n"
        f"  • annotated.md           带「▸ 图N」标记的文章，照着贴图即可\n\n"
        "把 images/ 里的 PNG 按 annotated.md 的标记贴进公众号 / 小红书编辑器即可。"
    )
    if mock_image:
        body += (
            "\n\n[dim]当前是 mock 占位图（keyless）。运行 duanhui init 接入"
            " 通义万相 / 可灵 / 即梦 / SeeDream 后即可出真图。[/dim]"
        )
    console.print(Panel(body, title="导出完成", border_style="green"))


@app.command()
def init(
    path: Optional[Path] = typer.Option(
        None,
        "--path",
        help="配置文件位置（默认 ~/.duanhui/config.yaml）。",
    ),
) -> None:
    """交互式写入配置：选择 LLM / 图像后端并粘贴 key（可跳过，留在 mock）。"""
    cfg_path = path or default_config_path()
    console.print(
        Panel(
            "选择放置（LLM）和出图（image）后端并粘贴对应 key。\n"
            "直接回车可保留默认；不填 key 则该后端在 mock 模式下运行（keyless）。",
            title="duanhui init",
            border_style="cyan",
        )
    )

    llm_choices = available_llm_backends()
    image_choices = available_image_backends()
    console.print(f"可选 LLM 后端：{', '.join(llm_choices)}")
    llm_backend = typer.prompt("LLM 后端", default=DEFAULT_LLM_BACKEND).strip()
    console.print(f"可选 图像 后端：{', '.join(image_choices)}")
    image_backend = typer.prompt("图像 后端", default=DEFAULT_IMAGE_BACKEND).strip()

    llm_key = typer.prompt(
        f"{llm_backend} API key（留空则该后端走 mock）",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()
    image_key = typer.prompt(
        f"{image_backend} API key（留空则该后端走 mock）",
        default="",
        hide_input=True,
        show_default=False,
    ).strip()

    cfg = Config(
        llm_backend=llm_backend or DEFAULT_LLM_BACKEND,
        image_backend=image_backend or DEFAULT_IMAGE_BACKEND,
        llm_api_key=llm_key or None,
        image_api_key=image_key or None,
    )
    written = cfg.save(cfg_path)
    console.print(f"[green]✓[/green] 已写入配置：[bold]{written}[/bold]")
    if not (llm_key and image_key):
        console.print(
            "[dim]提示：未填的 key 对应的后端将以 mock 运行，可随时重跑 init 补上。[/dim]"
        )


@app.command()
def version() -> None:
    """打印 段绘 版本号。"""
    console.print(f"duanhui {__version__}")


@app.command()
def backends() -> None:
    """列出可选的 LLM / 图像后端。"""
    table = Table(title="可选后端", header_style="bold")
    table.add_column("家族", style="cyan", no_wrap=True)
    table.add_column("后端")
    table.add_column("说明", style="dim")
    table.add_row("LLM 放置", ", ".join(available_llm_backends()), "决定在哪配图、配什么")
    table.add_row("图像 出图", ", ".join(available_image_backends()), "把每个配图点渲染成 PNG")
    console.print(table)
    console.print("[dim]mock 后端无需任何 key，用于 --dry-run 与 CI。[/dim]")


@app.command(name="list-styles")
def list_styles() -> None:
    """列出可选的风格包（v0.2.0：默认 guaidan，另含 shuimo 水墨）。"""
    table = Table(title="可选风格包", header_style="bold")
    table.add_column("pack_id", style="cyan", no_wrap=True)
    table.add_column("名称")
    table.add_column("说明", style="dim")
    for pid in available_style_packs():
        try:
            pack = load_style_pack(pack_id=pid)
            table.add_row(
                pid,
                pack.display_name or pid,
                f"{pack.motif} · seed={pack.consistency_seed} · {pack.aspect_ratio}",
            )
        except Exception:
            table.add_row(pid, "—", "[red]读取失败[/red]")
    console.print(table)
    console.print(
        "[dim]用 duanhui run --style <pack_id> 选择风格包；"
        "每篇文章仍锁定单一风格（同 seed + 同 preamble），整批同手。[/dim]"
    )


if __name__ == "__main__":  # pragma: no cover
    app()
