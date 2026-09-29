#!/usr/bin/env python3
"""Entry point (REQUIREMENTS 62).

The implementation lives in ``src/quant/cli.py`` so that the same code backs
both this script and the installed ``quant-stock-selector`` console command.
Running from a source checkout needs no installation: the path is added here.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from quant.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
