"""Shared pytest fixtures and import bootstrap for the CLI tests.

`codestrain_cli.py` lives one level above this dir; expose it as the `cli`
module so tests can `import cli` without a packaging step.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_CLI_FILE = Path(__file__).resolve().parents[1] / "codestrain_cli.py"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_cli_module():
    spec = importlib.util.spec_from_file_location("codestrain_cli", _CLI_FILE)
    assert spec is not None and spec.loader is not None, f"cannot load {_CLI_FILE}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["codestrain_cli"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def cli():
    """The codestrain_cli module, loaded once per pytest session."""
    return _load_cli_module()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return _FIXTURES_DIR


@pytest.fixture(scope="session")
def projects_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "projects"
