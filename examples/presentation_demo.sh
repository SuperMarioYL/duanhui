#!/usr/bin/env bash
set -euo pipefail
DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --dry-run -n 3
DUANHUI_MOCK=1 python -m duanhui.cli run examples/sample_article.md --cover --dry-run
