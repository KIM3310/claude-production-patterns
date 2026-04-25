from __future__ import annotations

import compileall
from pathlib import Path


def test_all_patterns_compile() -> None:
    patterns_dir = Path(__file__).resolve().parents[1] / "patterns"
    assert compileall.compile_dir(str(patterns_dir), quiet=1)
