"""段绘（DuanHui） — 整篇中文文章批量同风格配图命令行工具。

DuanHui turns a whole pasted Chinese article into a batch of style-consistent
白底怪诞手绘 illustrations: it splits the article into role-tagged paragraph
segments, lets an LLM backend decide *where* to illustrate and *what each
picture depicts*, locks every render to one native aesthetic, and exports a
drop-in bundle for 公众号 / 小红书.

Every stage runs keyless via mock backends so the full pipeline is observable
without any API key.
"""

__version__ = "0.5.0"

__all__ = ["__version__"]
