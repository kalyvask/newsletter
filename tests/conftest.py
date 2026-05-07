"""Test fixtures for the deployment-monitor agent layer.

Each test gets an isolated SQLite DB by monkeypatching `src.database.DATABASE_PATH`
to point at a tmp file. The base schema (`init_database`) and the agent schema
(`init_agent_schema`) are both initialized so any test can use either layer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.database as db_module


@pytest.fixture
def tmp_db(tmp_path, monkeypatch) -> Path:
    """Isolated SQLite DB. The base + agent schema are pre-initialized."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DATABASE_PATH", db_path)

    # Late imports so they pick up the monkeypatched path.
    from src.database import init_database
    from src.agent.schema import init_agent_schema

    init_database()
    init_agent_schema()
    return db_path
