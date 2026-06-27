# vhs script — the DuanHui 10-minute happy path, rendered keyless.
#
# Render locally with:   vhs docs/demo.tape
# CI re-renders it via .github/workflows/demo.yml (manual dispatch).
#
# It drives the REAL `duanhui` CLI on the shipped sample article, entirely on
# the mock backends (DUANHUI_MOCK=1), so the gif shows the whole pipeline —
# placement plan -> style-locked render -> drop-in export bundle — with no keys.

Output assets/demo.gif

Set Shell "bash"
Set FontSize 17
Set Width 1200
Set Height 760
Set Padding 24
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

# keyless: force every backend to its mock so the demo needs no API key.
Hide
Type "export DUANHUI_MOCK=1 && clear" Enter
Show
Sleep 600ms

Type "# 1) preview the plan — which paragraphs get a picture, what each depicts (keyless)" Enter
Sleep 700ms
Type "duanhui run examples/sample_article.md --dry-run" Enter
Sleep 4s

Type "# 2) full run: segment -> place -> style-lock -> render batch -> export" Enter
Sleep 700ms
Type "duanhui run examples/sample_article.md -o out/" Enter
Sleep 4s

Type "# 3) the drop-in bundle: a PNG batch + placement_map.json + annotated.md" Enter
Sleep 700ms
Type "ls out/ && ls out/images/" Enter
Sleep 3s

Type "# every PNG shares one locked 怪诞手绘 style (consistency_seed)." Enter
Sleep 700ms
Type "head -n 14 out/placement_map.json" Enter
Sleep 3500ms
