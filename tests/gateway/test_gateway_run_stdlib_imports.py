"""Regression coverage for stdlib names used during gateway startup."""

import ast
from pathlib import Path


def test_gateway_run_imports_faulthandler():
    run_path = Path(__file__).resolve().parents[2] / "gateway" / "run.py"
    tree = ast.parse(run_path.read_text(encoding="utf-8"))

    imported_names = {
        alias.name
        for node in tree.body if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "faulthandler" in imported_names
