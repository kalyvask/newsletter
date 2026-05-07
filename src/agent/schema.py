"""Schema additions for the agent layer.

Idempotent. Safe to call on every CLI invocation. Adds:

- `narratives`: one row per active or resolved narrative.
- `narrative_evidence`: many-to-one to articles. Each row is one piece of
  evidence supporting / elaborating / contradicting a narrative.
- ALTER `opportunity_signals` to add validation tracking columns. The
  existing detection code keeps working unchanged; the agent reads/writes
  the new columns to track each signal as a lead.
"""

from __future__ import annotations

import logging

from src.database import db_connection

logger = logging.getLogger(__name__)


def init_agent_schema() -> None:
    """Create agent tables and columns. Idempotent."""
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS narratives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                thesis TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'emerging',
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_evidence_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                evidence_count INTEGER DEFAULT 0,
                evolution_log TEXT,
                resolved_reason TEXT,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS narrative_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                narrative_id INTEGER NOT NULL,
                article_id INTEGER NOT NULL,
                contribution TEXT NOT NULL,
                summary TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (narrative_id) REFERENCES narratives(id),
                FOREIGN KEY (article_id) REFERENCES articles(id),
                UNIQUE(narrative_id, article_id)
            )
        """)

        # Lead validation columns on the existing opportunity_signals table.
        # The detection path in analyzer.py is unaffected — it writes the
        # original columns; the agent reads/writes the new ones.
        for col_def in [
            "ALTER TABLE opportunity_signals ADD COLUMN validation_status TEXT DEFAULT 'unconfirmed'",
            "ALTER TABLE opportunity_signals ADD COLUMN corroboration_count INTEGER DEFAULT 0",
            "ALTER TABLE opportunity_signals ADD COLUMN corroborating_article_ids TEXT",
            "ALTER TABLE opportunity_signals ADD COLUMN last_checked_at TIMESTAMP",
            "ALTER TABLE opportunity_signals ADD COLUMN dead_at TIMESTAMP",
            "ALTER TABLE opportunity_signals ADD COLUMN agent_notes TEXT",
        ]:
            try:
                cursor.execute(col_def)
            except Exception:
                pass  # Column exists.

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_narratives_status
            ON narratives(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_narratives_last_evidence
            ON narratives(last_evidence_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_narrative_evidence_narrative
            ON narrative_evidence(narrative_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_narrative_evidence_article
            ON narrative_evidence(article_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_oppsig_validation
            ON opportunity_signals(validation_status)
        """)

    logger.debug("agent_schema_initialized")
