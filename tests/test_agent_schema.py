"""Tests for the agent schema additions.

Covers idempotency (calling init twice is safe) and that all expected
tables/columns exist after init.
"""

from __future__ import annotations

import sqlite3

from src.agent.schema import init_agent_schema


def test_init_agent_schema_creates_narratives_table(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cursor.fetchall()}
    conn.close()
    assert "narratives" in tables
    assert "narrative_evidence" in tables


def test_init_agent_schema_adds_validation_columns_to_opportunity_signals(tmp_db):
    conn = sqlite3.connect(str(tmp_db))
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(opportunity_signals)")
    cols = {r[1] for r in cursor.fetchall()}
    conn.close()
    for required in (
        "validation_status",
        "corroboration_count",
        "corroborating_article_ids",
        "last_checked_at",
        "dead_at",
        "agent_notes",
    ):
        assert required in cols, f"missing column: {required}"


def test_init_agent_schema_is_idempotent(tmp_db):
    """Calling init a second time must not raise."""
    init_agent_schema()
    init_agent_schema()
    # if we got here, no exceptions were raised
