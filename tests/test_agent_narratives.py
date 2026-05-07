"""Tests for the narrative-tracking agent module.

LLM calls are mocked. The DB is a tmp SQLite via the `tmp_db` fixture.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agent.narratives import (
    NarrativeStatus,
    create_narrative,
    decay_quiet_narratives,
    get_narrative,
    get_narrative_evidence,
    list_narratives,
    match_against_ledger,
    process_recent_articles,
    update_narrative,
)
from src.database import db_connection


# ---- helpers -----------------------------------------------------------


def _seed_article(title: str, summary: str = "", content: str = "") -> int:
    """Insert a minimal article row directly. Returns its id."""
    with db_connection() as conn:
        cursor = conn.cursor()
        # Need a source row first.
        cursor.execute(
            "INSERT OR IGNORE INTO sources (id, name, type, url) VALUES (1, 'test', 'rss', 'http://x')"
        )
        cursor.execute(
            """
            INSERT INTO articles (source_id, title, url, content_summary, full_content, relevance_score, is_processed)
            VALUES (1, ?, ?, ?, ?, 0.8, 1)
            """,
            (title, f"http://example.com/{title.lower().replace(' ', '-')}", summary, content),
        )
        return cursor.lastrowid


def _set_narrative_last_evidence(narrative_id: int, when: datetime) -> None:
    """Backdate a narrative's last_evidence_at for decay tests."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE narratives SET last_evidence_at = ? WHERE id = ?",
            (when.isoformat(), narrative_id),
        )


# ---- create_narrative --------------------------------------------------


def test_create_narrative_with_explicit_title_seeds_evidence_row(tmp_db):
    article_id = _seed_article("Anthropic doubles inference capacity")
    article = {"id": article_id, "title": "Anthropic doubles inference capacity", "content_summary": ""}

    nid = create_narrative(
        article,
        title="Anthropic capacity ramp",
        thesis="Anthropic is shipping capacity faster than demand.",
    )

    n = get_narrative(nid)
    assert n is not None
    assert n["title"] == "Anthropic capacity ramp"
    assert n["status"] == NarrativeStatus.EMERGING.value
    # Seed evidence row was inserted, so evidence_count should be 1.
    assert n["evidence_count"] == 1

    evidence = get_narrative_evidence(nid)
    assert len(evidence) == 1
    assert evidence[0]["article_id"] == article_id
    assert evidence[0]["contribution"] == "new"


def test_create_narrative_without_title_calls_llm(tmp_db):
    article_id = _seed_article("OpenAI announces enterprise deal with Walmart")
    article = {
        "id": article_id,
        "title": "OpenAI announces enterprise deal with Walmart",
        "content_summary": "10K stores get GPT-4o licenses.",
    }
    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "title": "OpenAI Walmart deal",
        "thesis": "OpenAI lands its largest enterprise retail rollout yet.",
    }

    nid = create_narrative(article, llm=fake_llm)
    n = get_narrative(nid)
    assert n["title"] == "OpenAI Walmart deal"
    assert n["thesis"] == "OpenAI lands its largest enterprise retail rollout yet."
    fake_llm.call_json.assert_called_once()


# ---- match_against_ledger ----------------------------------------------


def test_match_against_ledger_extends_existing_narrative_when_llm_says_extend(tmp_db):
    a1 = _seed_article("Anthropic ships new region")
    nid = create_narrative(
        {"id": a1, "title": "Anthropic ships new region", "content_summary": ""},
        title="Anthropic capacity ramp",
        thesis="Anthropic is shipping capacity faster than demand.",
    )

    new_article_id = _seed_article("Anthropic adds Tokyo region")
    new_article = {
        "id": new_article_id,
        "title": "Anthropic adds Tokyo region",
        "content_summary": "Capacity expansion continues.",
    }

    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "decision": "extend",
        "narrative_id": nid,
        "contribution": "new_evidence",
        "summary": "Tokyo region adds APAC capacity.",
        "confidence": 0.85,
        "reasoning": "Same company, same capacity-shipping narrative.",
        "proposed_title": "",
        "proposed_thesis": "",
    }

    match = match_against_ledger(new_article, llm=fake_llm)
    assert match.narrative_id == nid
    assert match.contribution == "new_evidence"
    assert match.confidence > 0.5


def test_match_against_ledger_returns_new_when_llm_proposes_new(tmp_db):
    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "decision": "new",
        "narrative_id": None,
        "contribution": "new",
        "summary": "Mistral funding round.",
        "confidence": 0.9,
        "reasoning": "Different company, different topic.",
        "proposed_title": "Mistral funding",
        "proposed_thesis": "Mistral is raising at higher valuations than US peers.",
    }
    article_id = _seed_article("Mistral raises Series C")
    match = match_against_ledger(
        {"id": article_id, "title": "Mistral raises Series C", "content_summary": ""},
        llm=fake_llm,
    )
    assert match.narrative_id is None
    assert match.contribution == "new"


def test_match_against_ledger_degrades_to_new_when_invalid_id_returned(tmp_db):
    """If the LLM hallucinates a narrative_id that isn't on the ledger, fall back to new."""
    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "decision": "extend",
        "narrative_id": 99999,
        "contribution": "new_evidence",
        "summary": "Some summary.",
        "confidence": 0.7,
        "reasoning": "n/a",
    }
    article_id = _seed_article("Some article")
    match = match_against_ledger(
        {"id": article_id, "title": "Some article", "content_summary": ""},
        llm=fake_llm,
    )
    assert match.narrative_id is None
    assert match.contribution == "new"


def test_match_against_ledger_handles_llm_failure(tmp_db):
    """When the LLM call returns None, match returns a conservative new fallback."""
    fake_llm = MagicMock()
    fake_llm.call_json.return_value = None
    article_id = _seed_article("X")
    match = match_against_ledger(
        {"id": article_id, "title": "X", "content_summary": ""},
        llm=fake_llm,
    )
    assert match.narrative_id is None
    assert match.contribution == "new"
    assert match.confidence == 0.0


# ---- update_narrative --------------------------------------------------


def test_update_narrative_appends_evidence_and_promotes_emerging_to_active(tmp_db):
    a1 = _seed_article("First evidence")
    nid = create_narrative(
        {"id": a1, "title": "First evidence", "content_summary": ""},
        title="Test narrative",
        thesis="A test thesis.",
    )

    a2 = _seed_article("Second evidence")
    ok = update_narrative(
        nid,
        {"id": a2, "title": "Second evidence", "content_summary": ""},
        contribution="new_evidence",
        summary="More data.",
    )
    assert ok is True

    n = get_narrative(nid)
    # Two pieces of evidence -> auto-promote from emerging to active.
    assert n["status"] == NarrativeStatus.ACTIVE.value
    assert n["evidence_count"] == 2

    evidence = get_narrative_evidence(nid)
    assert len(evidence) == 2


def test_update_narrative_is_idempotent_for_same_article(tmp_db):
    a1 = _seed_article("Only article")
    nid = create_narrative(
        {"id": a1, "title": "Only article", "content_summary": ""},
        title="N",
        thesis="T",
    )

    # Updating with the same article id should be a no-op.
    second_call = update_narrative(
        nid,
        {"id": a1, "title": "Only article", "content_summary": ""},
        contribution="new_evidence",
        summary="duplicate",
    )
    assert second_call is False
    n = get_narrative(nid)
    assert n["evidence_count"] == 1


def test_update_narrative_with_contradicts_can_rewrite_thesis(tmp_db):
    a1 = _seed_article("Original")
    nid = create_narrative(
        {"id": a1, "title": "Original", "content_summary": ""},
        title="N",
        thesis="Inference costs are dropping.",
    )

    fake_llm = MagicMock()
    fake_llm.call_json.return_value = {
        "rewrite": True,
        "new_thesis": "Inference costs have plateaued.",
        "reason": "Counter-evidence from new article.",
    }

    a2 = _seed_article("Counter")
    update_narrative(
        nid,
        {"id": a2, "title": "Counter", "content_summary": ""},
        contribution="contradicts",
        summary="Costs flat for two quarters.",
        llm=fake_llm,
    )

    n = get_narrative(nid)
    assert "plateaued" in n["thesis"].lower()


# ---- decay_quiet_narratives -------------------------------------------


def test_decay_moves_emerging_to_plateauing_after_threshold(tmp_db):
    a1 = _seed_article("X")
    nid = create_narrative(
        {"id": a1, "title": "X", "content_summary": ""},
        title="N",
        thesis="T",
    )
    _set_narrative_last_evidence(nid, datetime.utcnow() - timedelta(days=30))

    counts = decay_quiet_narratives(plateau_after_days=14, resolve_after_days=60)
    assert counts["plateaued"] == 1
    assert get_narrative(nid)["status"] == NarrativeStatus.PLATEAUING.value


def test_decay_resolves_old_narratives(tmp_db):
    a1 = _seed_article("X")
    nid = create_narrative(
        {"id": a1, "title": "X", "content_summary": ""},
        title="N",
        thesis="T",
    )
    _set_narrative_last_evidence(nid, datetime.utcnow() - timedelta(days=120))

    counts = decay_quiet_narratives(plateau_after_days=14, resolve_after_days=60)
    assert counts["resolved"] == 1
    assert get_narrative(nid)["status"] == NarrativeStatus.RESOLVED.value


# ---- process_recent_articles ------------------------------------------


def test_process_recent_articles_routes_extends_and_creates(tmp_db):
    a1 = _seed_article("Seed")
    seed_nid = create_narrative(
        {"id": a1, "title": "Seed", "content_summary": ""},
        title="Seed narrative",
        thesis="Tracking the seed thread.",
    )

    a2 = _seed_article("Extends seed")
    a3 = _seed_article("Brand new topic")

    fake_llm = MagicMock()

    def fake_call(prompt: str, system=None, max_tokens=1500) -> dict[str, Any]:
        if "Extends seed" in prompt:
            return {
                "decision": "extend",
                "narrative_id": seed_nid,
                "contribution": "elaborates",
                "summary": "Adds detail to the seed thread.",
                "confidence": 0.8,
                "reasoning": "Same subject as seed.",
            }
        if "Brand new topic" in prompt:
            return {
                "decision": "new",
                "narrative_id": None,
                "contribution": "new",
                "summary": "A new thread.",
                "confidence": 0.9,
                "reasoning": "Different subject.",
                "proposed_title": "New thread",
                "proposed_thesis": "Tracking a new thing.",
            }
        # For any thesis-rewrite or naming sub-call, fall through.
        if "rewrite" in prompt:
            return {"rewrite": False, "new_thesis": "", "reason": ""}
        return {"title": "Brand new", "thesis": "Tracking new thing."}

    fake_llm.call_json.side_effect = fake_call

    counts = process_recent_articles(
        [
            {"id": a2, "title": "Extends seed", "content_summary": ""},
            {"id": a3, "title": "Brand new topic", "content_summary": ""},
        ],
        llm=fake_llm,
    )

    assert counts["created"] == 1
    assert counts["extended"] == 1
    assert counts["skipped"] == 0
    assert len(list_narratives()) == 2


def test_process_recent_articles_skips_articles_without_title(tmp_db):
    fake_llm = MagicMock()
    counts = process_recent_articles(
        [{"id": 1, "title": "", "content_summary": ""}],
        llm=fake_llm,
    )
    assert counts["skipped"] == 1
    assert counts["created"] == 0
    fake_llm.call_json.assert_not_called()
