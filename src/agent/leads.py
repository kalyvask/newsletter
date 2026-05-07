"""Tracked-lead validation for opportunity signals.

The existing pipeline already detects opportunity signals via Claude's
`analyze` step (`processors/analyzer.py` writes them to
`opportunity_signals`). Today they sit in the database and do nothing.

This module turns each signal into a *tracked lead*: an unconfirmed claim
that is checked against fresh articles each cycle and either corroborated,
left open, or marked dead after going quiet for too long.

Capabilities (each is a discrete function):

- `register_signal_as_lead(signal_id)`: idempotent. Marks an existing
  opportunity_signals row as a tracked lead with `validation_status =
  'unconfirmed'`. Called from the analyze step (or backfilled).
- `validate_open_leads(new_articles)`: for each open lead, ask the LLM
  whether any new article corroborates it. Increments `corroboration_count`,
  flips status to `corroborated` after a configurable threshold.
- `auto_dead_stale_leads(stale_days=...)`: bulk-mark unconfirmed leads dead
  if they've gone too long without corroboration.
- `list_open_leads(...)`: surface for the CLI / daemon.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from src.agent.llm import AgentLLM, AgentLLMError
from src.database import db_connection

logger = logging.getLogger(__name__)


# A lead becomes 'corroborated' once it has this many distinct corroborating articles.
DEFAULT_CORROBORATION_THRESHOLD = 2

# Unconfirmed leads with no corroboration in this many days are marked dead.
DEFAULT_LEAD_STALE_DAYS = 14


class LeadStatus(str, Enum):
    UNCONFIRMED = "unconfirmed"
    CORROBORATED = "corroborated"
    DEAD = "dead"


OPEN_LEAD_STATUSES = (LeadStatus.UNCONFIRMED.value, LeadStatus.CORROBORATED.value)


# ---- storage -------------------------------------------------------------


def _row_to_dict(row) -> dict:
    return dict(row)


def get_lead(signal_id: int) -> Optional[dict]:
    """Fetch a signal-as-lead row by id."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM opportunity_signals WHERE id = ?", (signal_id,)
        )
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None


def list_open_leads(
    *,
    min_strength: float = 0.0,
    limit: int = 50,
) -> list[dict]:
    """List leads that are still worth attention."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT os.*, a.title AS article_title, a.url AS article_url,
                   a.published_date AS article_published_date
            FROM opportunity_signals os
            LEFT JOIN articles a ON os.article_id = a.id
            WHERE os.validation_status IN ('unconfirmed', 'corroborated')
              AND os.signal_strength >= ?
            ORDER BY
                CASE os.validation_status
                    WHEN 'corroborated' THEN 0
                    ELSE 1
                END,
                os.signal_strength DESC,
                os.created_at DESC
            LIMIT ?
            """,
            (min_strength, limit),
        )
        return [_row_to_dict(r) for r in cursor.fetchall()]


def register_signal_as_lead(signal_id: int) -> bool:
    """Mark an existing signal as a tracked lead. Idempotent.

    Returns True if state changed (validation_status was NULL/missing),
    False if it was already tracked.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT validation_status FROM opportunity_signals WHERE id = ?",
            (signal_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return False
        current = row["validation_status"]
        if current in (LeadStatus.UNCONFIRMED.value, LeadStatus.CORROBORATED.value, LeadStatus.DEAD.value):
            return False

        cursor.execute(
            """
            UPDATE opportunity_signals
            SET validation_status = ?,
                last_checked_at = ?
            WHERE id = ?
            """,
            (LeadStatus.UNCONFIRMED.value, datetime.utcnow().isoformat(), signal_id),
        )
    return True


def backfill_existing_signals_as_leads() -> int:
    """One-shot: register every existing opportunity_signals row as a lead.

    Useful right after rolling out the agent for the first time. Returns the
    number of rows updated.
    """
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE opportunity_signals
            SET validation_status = 'unconfirmed',
                last_checked_at = ?
            WHERE validation_status IS NULL
            """,
            (datetime.utcnow().isoformat(),),
        )
        return cursor.rowcount


def _record_corroboration(
    lead_id: int,
    article_id: int,
    *,
    corroborated_now: bool,
    new_threshold_count: int,
) -> None:
    """Append `article_id` to corroborating list and update status if needed."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT corroborating_article_ids, corroboration_count
            FROM opportunity_signals
            WHERE id = ?
            """,
            (lead_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return

        try:
            ids = json.loads(row["corroborating_article_ids"] or "[]")
        except json.JSONDecodeError:
            ids = []

        if article_id in ids:
            # Already counted this article. Just bump last_checked_at.
            cursor.execute(
                "UPDATE opportunity_signals SET last_checked_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), lead_id),
            )
            return

        ids.append(article_id)
        new_count = (row["corroboration_count"] or 0) + 1

        sets = [
            "corroborating_article_ids = ?",
            "corroboration_count = ?",
            "last_checked_at = ?",
        ]
        params: list[Any] = [
            json.dumps(ids),
            new_count,
            datetime.utcnow().isoformat(),
        ]
        if corroborated_now and new_count >= new_threshold_count:
            sets.append("validation_status = ?")
            params.append(LeadStatus.CORROBORATED.value)

        params.append(lead_id)
        cursor.execute(
            f"UPDATE opportunity_signals SET {', '.join(sets)} WHERE id = ?",
            params,
        )


def _record_check_only(lead_id: int) -> None:
    """No corroboration found this round, just update the timestamp."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE opportunity_signals SET last_checked_at = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), lead_id),
        )


# ---- prompt --------------------------------------------------------------


_VALIDATION_SYSTEM = (
    "You assess whether a candidate news article corroborates an existing "
    "open business intelligence lead. Be strict: corroboration requires the "
    "article to refer to the SAME company and the SAME concrete event or "
    "phenomenon. Tangentially-related articles do NOT count."
)


def _build_validation_prompt(lead: dict, article: dict) -> str:
    return f"""
Open lead:
- id: {lead.get("id")}
- type: {lead.get("signal_type")}
- company: {lead.get("company_name")}
- summary: {lead.get("opportunity_summary")}
- first seen: {lead.get("created_at")}

Candidate article:
- title: {article.get("title", "")}
- url: {article.get("url", "")}
- summary: {article.get("content_summary", "") or ""}

Does this article corroborate the lead? Corroboration means: the article
mentions the same company AND describes evidence consistent with the lead's
type and summary. A vague mention of the company does not count.

Respond with JSON only:

{{
  "corroborates": true | false,
  "evidence_quote": "<short quoted phrase from the article that corroborates, or empty>",
  "reasoning": "<one sentence>"
}}
""".strip()


# ---- core capabilities ---------------------------------------------------


def validate_open_leads(
    new_articles: list[dict],
    *,
    llm: Optional[AgentLLM] = None,
    threshold: int = DEFAULT_CORROBORATION_THRESHOLD,
) -> dict[str, int]:
    """For each open lead, see if any of `new_articles` corroborates it.

    Returns counts: leads_checked, articles_checked, corroborations_added,
    leads_promoted (to corroborated this run).

    To keep token spend bounded, we only ask the LLM for article+lead pairs
    that look like a candidate match by company name. Leads with no company
    name are skipped (we have no anchor to compare).
    """
    llm = llm or AgentLLM()
    leads = list_open_leads(limit=200)

    counts = {
        "leads_checked": 0,
        "articles_checked": 0,
        "corroborations_added": 0,
        "leads_promoted": 0,
    }

    for lead in leads:
        company = (lead.get("company_name") or "").strip()
        if not company:
            continue

        counts["leads_checked"] += 1
        promoted_this_round = False

        company_lower = company.lower()
        for article in new_articles:
            # Skip self-reference: the article that originally created the lead.
            if article.get("id") == lead.get("article_id"):
                continue

            # Cheap pre-filter on company name presence in title or summary.
            text_blob = (
                (article.get("title", "") or "")
                + " "
                + (article.get("content_summary", "") or "")
            ).lower()
            if company_lower not in text_blob:
                continue

            counts["articles_checked"] += 1

            try:
                resp = llm.call_json(
                    _build_validation_prompt(lead, article),
                    system=_VALIDATION_SYSTEM,
                    max_tokens=300,
                )
            except AgentLLMError:
                resp = None
            if resp is None:
                continue

            if not resp.get("corroborates"):
                continue

            # Re-fetch lead to read up-to-date counts (other articles in this
            # batch may have already corroborated).
            current = get_lead(lead["id"])
            if current is None:
                continue
            already_corroborated = (
                current["validation_status"] == LeadStatus.CORROBORATED.value
            )

            _record_corroboration(
                lead_id=lead["id"],
                article_id=article["id"],
                corroborated_now=not already_corroborated,
                new_threshold_count=threshold,
            )
            counts["corroborations_added"] += 1
            if not already_corroborated:
                # Check if this corroboration just promoted the lead.
                refreshed = get_lead(lead["id"])
                if (
                    refreshed
                    and refreshed["validation_status"] == LeadStatus.CORROBORATED.value
                    and not promoted_this_round
                ):
                    counts["leads_promoted"] += 1
                    promoted_this_round = True

        # If the lead was checked but no corroboration was found, still
        # update last_checked_at so it doesn't get re-checked too often.
        if not promoted_this_round:
            _record_check_only(lead["id"])

    return counts


def auto_dead_stale_leads(
    *,
    stale_days: int = DEFAULT_LEAD_STALE_DAYS,
) -> int:
    """Mark unconfirmed leads with no corroboration in `stale_days` as dead.

    Corroborated leads are never auto-killed (they need a human to close them).
    Returns the number of leads transitioned to dead.
    """
    cutoff = (datetime.utcnow() - timedelta(days=stale_days)).isoformat()
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE opportunity_signals
            SET validation_status = 'dead',
                dead_at = ?,
                agent_notes = COALESCE(agent_notes, '') ||
                              CASE WHEN agent_notes IS NULL OR agent_notes = ''
                                   THEN ?
                                   ELSE ?
                              END
            WHERE validation_status = 'unconfirmed'
              AND created_at < ?
              AND corroboration_count = 0
            """,
            (
                datetime.utcnow().isoformat(),
                f"auto-dead after {stale_days}d without corroboration",
                f"; auto-dead after {stale_days}d without corroboration",
                cutoff,
            ),
        )
        return cursor.rowcount
