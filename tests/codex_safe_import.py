#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
path = repo / "bin" / "codex-safe"
loader = SourceFileLoader("codex_safe_script", str(path))
spec = importlib.util.spec_from_loader("codex_safe_script", loader)
if spec is None or spec.loader is None:
    raise RuntimeError("could not import codex-safe")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

migration_reason_for = module.migration_reason_for
