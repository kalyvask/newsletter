"""Tests for the lead-validation agent module.

LLM calls are mocked. The DB is a tmp SQLite via the `tmp_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.agent.leads import (
    LeadStatus,
    auto_dead_stale_leads,
    backfill_existing_signals_as_leads,
    get_lead,
    list_open_leads,
    register_signal_as_lead,
    validate_open_leads,
)
from src.database import db_connection


# ---- helpers -----------------------------------------------------------


def _seed_article(title: str, summary: str = "") -> int:
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sources (id, name, type, url) VALUES (1, 'test', 'rss', 'http://x')"
        )
        cursor.execute(
            """
            INSERT INTO articles (source_id, title, url, content_summary, full_content, relevance_score, is_processed)
            VALUES (1, ?, ?, ?, ?, 0.8, 1)
            """,
            (title, f"http://example.com/{title.lower().replace(' ', '-')}", summary, summary),
        )
        return cursor.lastrowid


def _seed_signal(
    *,
    article_id: int,
    signal_type: str = "hiring_wave",
    company_name: str = "Anthropic",
    summary: str = "Anthropic is hiring at unusual pace.",
    strength: float = 0.7,
    created_at: datetime | None = None,
    null_validation_status: bool = False,
) -> int:
    """Insert an opportunity_signals row directly.

    By default the schema's DEFAULT clause sets validation_status='unconfirmed'.
    Pass `null_validation_status=True` to simulate pre-agent rows that need
    backfill.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        ts = (created_at or datetime.utcnow()).isoformat()
        cursor.execute(
            """
            INSERT INTO opportunity_signals
                (article_id, signal_type, signal_strength, company_name,
                 opportunity_summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (article_id, signal_type, strength, company_name, summary, ts),
        )
        sid = cursor.lastrowid
        if null_validation_status:
            cursor.execute(
                "UPDATE opportunity_signals SET validation_status = NULL WHERE id = ?",
                (sid,),
            )
        return sid


# ---- backfill / register ----------------------------------------------


def test_signals_inserted_after_schema_migration_default_to_unconfirmed(tmp_db):
    """The schema's DEFAULT clause means every new signal is auto-tracked."""
    a = _seed_article("seed")
    sid = _seed_signal(article_id=a)

    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.UNCONFIRMED.value


def test_backfill_picks_up_pre_agent_signals_with_null_status(tmp_db):
    """Simulate signals that existed before the agent rolled out (NULL status)."""
    a = _seed_article("seed")
    sid = _seed_signal(article_id=a, null_validation_status=True)

    n = backfill_existing_signals_as_leads()
    assert n == 1
    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.UNCONFIRMED.value


def test_backfill_is_a_no_op_for_already_tracked_signals(tmp_db):
    a = _seed_article("seed")
    _seed_signal(article_id=a)  # auto-tracked via DEFAULT

    n = backfill_existing_signals_as_leads()
    assert n == 0


def test_register_signal_as_lead_returns_false_for_already_tracked(tmp_db):
    """Auto-tracked signals (DEFAULT 'unconfirmed') don't need re-registration."""
    a = _seed_article("seed")
    sid = _seed_signal(article_id=a)

    # Already 'unconfirmed' from the DEFAULT — register_signal_as_lead is a no-op.
    assert register_signal_as_lead(sid) is False


def test_register_signal_as_lead_promotes_null_status_to_unconfirmed(tmp_db):
    """The function's real job: handle pre-agent rows that have NULL status."""
    a = _seed_article("seed")
    sid = _seed_signal(article_id=a, null_validation_status=True)

    assert register_signal_as_lead(sid) is True
    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.UNCONFIRMED.value


# ---- list_open_leads ---------------------------------------------------


def test_list_open_leads_excludes_dead(tmp_db):
    a1 = _seed_article("a1")
    a2 = _seed_article("a2")
    s1 = _seed_signal(article_id=a1, company_name="A")
    s2 = _seed_signal(article_id=a2, company_name="B")

    # Mark s1 dead manually.
    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE opportunity_signals SET validation_status = 'dead' WHERE id = ?",
            (s1,),
        )
        cur.execute(
            "UPDATE opportunity_signals SET validation_status = 'unconfirmed' WHERE id = ?",
            (s2,),
        )

    open_ids = [r["id"] for r in list_open_leads()]
    assert s2 in open_ids
    assert s1 not in open_ids


def test_list_open_leads_orders_corroborated_above_unconfirmed(tmp_db):
    a1 = _seed_article("a1")
    a2 = _seed_article("a2")
    s1 = _seed_signal(article_id=a1, company_name="A", strength=0.5)
    s2 = _seed_signal(article_id=a2, company_name="B", strength=0.5)

    with db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE opportunity_signals SET validation_status = 'corroborated' WHERE id = ?",
            (s2,),
        )
        cur.execute(
            "UPDATE opportunity_signals SET validation_status = 'unconfirmed' WHERE id = ?",
            (s1,),
        )

    rows = list_open_leads()
    # Corroborated should come first.
    assert rows[0]["id"] == s2


# ---- validate_open_leads ----------------------------------------------


def test_validate_open_leads_increments_count_and_promotes_at_threshold(tmp_db):
    seed = _seed_article("Anthropic seed")
    sid = _seed_signal(article_id=seed, company_name="Anthropic")
    backfill_existing_signals_as_leads()

    a1 = _seed_article("Anthropic announces new tier", summary="Anthropic launches enterprise tier.")
    a2 = _seed_article("Anthropic hiring spree", summary="Anthropic posts 200 roles.")

    fake_llm = MagicMock()
    # Both articles corroborate the lead.
    fake_llm.call_json.return_value = {
        "corroborates": True,
        "evidence_quote": "Anthropic posts 200 roles.",
        "reasoning": "Direct corroboration.",
    }

    counts = validate_open_leads(
        [
            {"id": a1, "title": "Anthropic announces new tier", "content_summary": "Anthropic launches enterprise tier."},
            {"id": a2, "title": "Anthropic hiring spree", "content_summary": "Anthropic posts 200 roles."},
        ],
        llm=fake_llm,
        threshold=2,
    )

    assert counts["leads_checked"] == 1
    assert counts["corroborations_added"] == 2
    assert counts["leads_promoted"] == 1

    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.CORROBORATED.value
    assert lead["corroboration_count"] == 2


def test_validate_open_leads_skips_self_referencing_article(tmp_db):
    seed = _seed_article("Anthropic seed", summary="Anthropic posts roles.")
    sid = _seed_signal(article_id=seed, company_name="Anthropic")
    backfill_existing_signals_as_leads()

    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "corroborates": True,
        "evidence_quote": "x",
        "reasoning": "x",
    }

    counts = validate_open_leads(
        [{"id": seed, "title": "Anthropic seed", "content_summary": "Anthropic posts roles."}],
        llm=fake_llm,
    )
    # Should never have called the LLM because the only "candidate" was the
    # originating article.
    assert counts["articles_checked"] == 0
    fake_llm.call_json.assert_not_called()


def test_validate_open_leads_skips_articles_without_company_match(tmp_db):
    seed = _seed_article("seed", summary="x")
    _seed_signal(article_id=seed, company_name="UniqueCompanyName")
    backfill_existing_signals_as_leads()

    a = _seed_article("Some other unrelated article", summary="No mention of the company.")

    fake_llm = MagicMock()
    counts = validate_open_leads(
        [{"id": a, "title": "Some other unrelated article", "content_summary": "No mention."}],
        llm=fake_llm,
    )
    fake_llm.call_json.assert_not_called()
    assert counts["articles_checked"] == 0


def test_validate_open_leads_does_not_double_count_same_article(tmp_db):
    seed = _seed_article("Anthropic seed")
    sid = _seed_signal(article_id=seed, company_name="Anthropic")
    backfill_existing_signals_as_leads()

    a1 = _seed_article("Anthropic news 1", summary="Anthropic launches new feature.")

    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "corroborates": True,
        "evidence_quote": "x",
        "reasoning": "x",
    }

    article = {"id": a1, "title": "Anthropic news 1", "content_summary": "Anthropic launches new feature."}
    validate_open_leads([article], llm=fake_llm, threshold=10)
    validate_open_leads([article], llm=fake_llm, threshold=10)

    lead = get_lead(sid)
    # The same article id should not be counted twice.
    assert lead["corroboration_count"] == 1


# ---- auto_dead_stale_leads --------------------------------------------


def test_auto_dead_kills_unconfirmed_with_no_corroboration(tmp_db):
    seed = _seed_article("seed")
    sid = _seed_signal(
        article_id=seed,
        company_name="Anthropic",
        created_at=datetime.utcnow() - timedelta(days=30),
    )
    backfill_existing_signals_as_leads()

    n = auto_dead_stale_leads(stale_days=14)
    assert n == 1
    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.DEAD.value
    assert lead["dead_at"] is not None


def test_auto_dead_does_not_kill_corroborated_leads(tmp_db):
    seed = _seed_article("seed")
    sid = _seed_signal(
        article_id=seed,
        company_name="Anthropic",
        created_at=datetime.utcnow() - timedelta(days=30),
    )
    backfill_existing_signals_as_leads()
    with db_connection() as conn:
        conn.execute(
            "UPDATE opportunity_signals SET validation_status = 'corroborated' WHERE id = ?",
            (sid,),
        )

    n = auto_dead_stale_leads(stale_days=14)
    assert n == 0
    lead = get_lead(sid)
    assert lead["validation_status"] == LeadStatus.CORROBORATED.value


def test_auto_dead_spares_recent_unconfirmed(tmp_db):
    seed = _seed_article("seed")
    _seed_signal(
        article_id=seed,
        company_name="Anthropic",
        created_at=datetime.utcnow() - timedelta(days=2),
    )
    backfill_existing_signals_as_leads()

    n = auto_dead_stale_leads(stale_days=14)
    assert n == 0
