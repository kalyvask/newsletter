"""Narrative continuity layer.

Maintains a ledger of active narratives across reports. The batch pipeline
emits articles independently; this module groups them into evolving threads
("Anthropic capacity ramp", "Inference cost compression", "RAG pipelines
moving to per-app evals") so each new report can say "this is week 3 of X"
instead of treating week 3 as a fresh discovery.

Capabilities (each is a discrete function — same code path serves the CLI
and a future autonomous loop):

- `match_against_ledger(article)`: LLM decides whether the article extends
  an existing narrative, contradicts one, or is new.
- `create_narrative(article)`: spin up a new narrative from an article,
  with a short title and one-line thesis.
- `update_narrative(narrative_id, article, contribution)`: add evidence,
  update last_evidence_at, optionally rewrite the thesis if the new
  evidence shifts the story.
- `list_narratives(status=...)`: surface the ledger.
- `decay_quiet_narratives(stale_days=...)`: emerging -> plateauing ->
  resolved when no evidence arrives.
- `process_recent_articles(...)`: batch wrapper used by the CLI / daemon.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

from src.agent.llm import AgentLLM, AgentLLMError
from src.database import db_connection

logger = logging.getLogger(__name__)


# Narratives older than this with no new evidence move toward resolved.
DEFAULT_STALE_PLATEAU_DAYS = 14
DEFAULT_STALE_RESOLVE_DAYS = 35

# How many active narratives to feed into the matcher prompt at once. More
# context = better matches but bigger prompts. 25 is a reasonable cap.
MAX_LEDGER_FOR_MATCH = 25


class NarrativeStatus(str, Enum):
    EMERGING = "emerging"
    ACTIVE = "active"
    PLATEAUING = "plateauing"
    RESOLVED = "resolved"


# A narrative is "open" (still worth matching against) if not resolved.
OPEN_STATUSES = (
    NarrativeStatus.EMERGING.value,
    NarrativeStatus.ACTIVE.value,
    NarrativeStatus.PLATEAUING.value,
)


CONTRIBUTION_TYPES = {"new_evidence", "elaborates", "contradicts"}


@dataclass
class NarrativeMatch:
    """Result of matching one article against the ledger.

    `narrative_id` is None when the article is judged to be a new narrative.
    """

    narrative_id: Optional[int]
    contribution: str  # 'new_evidence' | 'elaborates' | 'contradicts' | 'new'
    summary: str       # 1-line summary of what the article adds
    confidence: float  # 0.0 - 1.0
    reasoning: str     # short LLM rationale (logged, not shown to users by default)


# ---- low-level storage helpers (kept in this file for cohesion) ----------


def _row_to_dict(row) -> dict:
    return dict(row)


def list_narratives(
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """List narratives, optionally filtered. Most recent activity first."""
    with db_connection() as conn:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT * FROM narratives
                WHERE status = ?
                ORDER BY last_evidence_at DESC
                LIMIT ?
                """,
                (status, limit),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM narratives
                ORDER BY last_evidence_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_row_to_dict(r) for r in cursor.fetchall()]


def get_narrative(narrative_id: int) -> Optional[dict]:
    """Fetch a single narrative row."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM narratives WHERE id = ?", (narrative_id,))
        row = cursor.fetchone()
        return _row_to_dict(row) if row else None


def get_narrative_evidence(narrative_id: int) -> list[dict]:
    """Get all evidence rows for a narrative, oldest first."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ne.*, a.title AS article_title, a.url AS article_url
            FROM narrative_evidence ne
            LEFT JOIN articles a ON ne.article_id = a.id
            WHERE ne.narrative_id = ?
            ORDER BY ne.added_at ASC
            """,
            (narrative_id,),
        )
        return [_row_to_dict(r) for r in cursor.fetchall()]


def _has_evidence_already(narrative_id: int, article_id: int) -> bool:
    """Idempotency guard: a given article can only be evidence once per narrative."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM narrative_evidence WHERE narrative_id = ? AND article_id = ?",
            (narrative_id, article_id),
        )
        return cursor.fetchone() is not None


def _insert_evidence(
    narrative_id: int,
    article_id: int,
    contribution: str,
    summary: str,
) -> Optional[int]:
    """Insert an evidence row. Returns id, or None if duplicate."""
    if _has_evidence_already(narrative_id, article_id):
        return None
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO narrative_evidence
                (narrative_id, article_id, contribution, summary)
            VALUES (?, ?, ?, ?)
            """,
            (narrative_id, article_id, contribution, summary),
        )
        return cursor.lastrowid


def _bump_narrative_after_evidence(
    narrative_id: int,
    *,
    new_status: Optional[str] = None,
    evolution_entry: Optional[dict] = None,
    new_thesis: Optional[str] = None,
) -> None:
    """Update last_evidence_at, evidence_count, and optionally status/thesis."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT evolution_log FROM narratives WHERE id = ?",
            (narrative_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return
        log_blob = row["evolution_log"] or "[]"
        try:
            log = json.loads(log_blob)
        except json.JSONDecodeError:
            log = []
        if evolution_entry is not None:
            log.append(evolution_entry)

        now = datetime.utcnow().isoformat()
        sets = [
            "last_evidence_at = ?",
            "evidence_count = evidence_count + 1",
            "evolution_log = ?",
            "updated_at = ?",
        ]
        params: list[Any] = [now, json.dumps(log), now]
        if new_status is not None:
            sets.append("status = ?")
            params.append(new_status)
        if new_thesis is not None:
            sets.append("thesis = ?")
            params.append(new_thesis)
        params.append(narrative_id)

        cursor.execute(
            f"UPDATE narratives SET {', '.join(sets)} WHERE id = ?",
            params,
        )


# ---- prompts -------------------------------------------------------------


_MATCH_SYSTEM = (
    "You are an analyst tracking evolving narratives across a research feed about "
    "AI deployment, enterprise adoption, and inference economics. Your job is to "
    "decide whether a new article belongs to an existing narrative thread, contradicts "
    "one, or starts a new thread. Be strict: a thread should require shared subject "
    "(same company, same product line, same specific phenomenon) — not just shared "
    "category."
)


def _build_match_prompt(article: dict, ledger: list[dict]) -> str:
    """Build the user prompt for match_against_ledger.

    The prompt asks Claude to either return a narrative_id from the ledger
    or to propose a new narrative. It must justify the decision briefly.
    """
    if ledger:
        ledger_block = "\n".join(
            f"  [{n['id']}] {n['title']} — {n['thesis']} "
            f"(status: {n['status']}, evidence_count: {n['evidence_count']})"
            for n in ledger
        )
    else:
        ledger_block = "  (ledger is empty — any new article starts a narrative)"

    title = article.get("title", "")
    summary = article.get("content_summary", "") or ""
    full = (article.get("full_content") or "")[:1500]

    return f"""
Active narrative ledger:
{ledger_block}

New article to classify:
- Title: {title}
- Summary: {summary}
- Excerpt: {full}

Decide one of:
1. The article *extends* an existing narrative with new_evidence, elaborates,
   or contradicts it.
2. The article starts a *new* narrative not on the ledger.

Respond ONLY with valid JSON, no preamble:

{{
  "decision": "extend" | "new",
  "narrative_id": <int from ledger if decision=extend, else null>,
  "contribution": "new_evidence" | "elaborates" | "contradicts" | "new",
  "summary": "<one sentence: what does this article add to the thread or, if new, what is the thread>",
  "confidence": <0.0 - 1.0>,
  "reasoning": "<one or two sentences justifying the decision; mention which existing narrative was the closest non-match if 'new'>",
  "proposed_title": "<short title for the narrative if decision=new, else empty string>",
  "proposed_thesis": "<one-sentence thesis for the narrative if decision=new, else empty string>"
}}

Be conservative about extending: if the overlap is only at the category level
(e.g., both about 'multimodal AI' but different companies), prefer 'new'.
""".strip()


_THESIS_REWRITE_SYSTEM = (
    "You are an analyst maintaining a narrative ledger. You will be given the "
    "current thesis of a narrative and a new piece of evidence. Decide whether "
    "the thesis still holds, needs an update, or has been falsified. Output only JSON."
)


def _build_thesis_rewrite_prompt(
    current_thesis: str, contribution: str, evidence_summary: str
) -> str:
    return f"""
Current narrative thesis:
"{current_thesis}"

New evidence contribution: {contribution}
Evidence summary: {evidence_summary}

Should the thesis change?

Respond with JSON only:

{{
  "rewrite": true | false,
  "new_thesis": "<rewritten thesis, only if rewrite=true; else empty string>",
  "reason": "<one short sentence>"
}}

Only set rewrite=true if the new evidence materially shifts the thesis (e.g.,
contradicts a quantitative claim, or adds a new component the thesis missed).
Don't rewrite for minor elaboration.
""".strip()


# ---- core capabilities ---------------------------------------------------


def match_against_ledger(
    article: dict,
    *,
    llm: Optional[AgentLLM] = None,
) -> NarrativeMatch:
    """Decide whether `article` extends an existing narrative or starts a new one.

    `article` is a dict with at least `title`, `content_summary`, and ideally
    `full_content`. The function only reads from the ledger and calls the LLM;
    it does not write to the database.
    """
    llm = llm or AgentLLM()
    ledger_full = list_narratives(limit=MAX_LEDGER_FOR_MATCH * 2)
    # Only show the agent narratives still worth matching against.
    ledger = [n for n in ledger_full if n["status"] in OPEN_STATUSES][:MAX_LEDGER_FOR_MATCH]

    response = llm.call_json(
        _build_match_prompt(article, ledger),
        system=_MATCH_SYSTEM,
        max_tokens=800,
    )
    if response is None:
        # Conservative fallback: treat as a new narrative we can't be sure about.
        return NarrativeMatch(
            narrative_id=None,
            contribution="new",
            summary=article.get("title", ""),
            confidence=0.0,
            reasoning="LLM call failed; treating as new narrative.",
        )

    decision = response.get("decision", "new")
    contribution = response.get("contribution", "new")
    if contribution not in CONTRIBUTION_TYPES and decision == "extend":
        contribution = "new_evidence"
    summary = response.get("summary") or article.get("title", "")
    confidence = float(response.get("confidence") or 0.0)
    reasoning = response.get("reasoning", "")

    if decision == "extend":
        nid = response.get("narrative_id")
        try:
            nid_int = int(nid) if nid is not None else None
        except (ValueError, TypeError):
            nid_int = None
        # Validate that the proposed id is actually on the ledger.
        if nid_int is not None and not any(n["id"] == nid_int for n in ledger):
            logger.warning(
                "narrative_match_invalid_id",
                extra={"proposed": nid, "title": article.get("title")},
            )
            nid_int = None
        if nid_int is None:
            # LLM said extend but didn't give a valid id; degrade to new.
            return NarrativeMatch(
                narrative_id=None,
                contribution="new",
                summary=summary,
                confidence=confidence,
                reasoning=reasoning + " (id was invalid; degraded to new)",
            )
        return NarrativeMatch(
            narrative_id=nid_int,
            contribution=contribution,
            summary=summary,
            confidence=confidence,
            reasoning=reasoning,
        )

    # decision == "new"
    return NarrativeMatch(
        narrative_id=None,
        contribution="new",
        summary=summary,
        confidence=confidence,
        reasoning=reasoning,
    )


def create_narrative(
    article: dict,
    *,
    title: Optional[str] = None,
    thesis: Optional[str] = None,
    llm: Optional[AgentLLM] = None,
    initial_summary: Optional[str] = None,
) -> int:
    """Create a new narrative seeded from one article. Returns the new id.

    If `title` and `thesis` aren't provided, asks the LLM to generate them.
    Always inserts the article as the first piece of evidence.
    """
    if title is None or thesis is None:
        llm = llm or AgentLLM()
        prompt = f"""
Given this article, propose a short narrative title and a one-sentence thesis.

Article title: {article.get("title", "")}
Article summary: {article.get("content_summary", "")}

Respond with JSON only:
{{
  "title": "<3-7 word narrative title>",
  "thesis": "<one specific sentence stating what the narrative is tracking>"
}}

The title names the thread; the thesis states the claim or phenomenon being tracked.
""".strip()
        resp = llm.call_json(
            prompt,
            system=(
                "You name and frame narrative threads about AI deployment trends. "
                "Be specific. 'AI is changing fast' is a bad thesis. "
                "'Anthropic is shipping Claude inference capacity faster than demand can grow' is a good one."
            ),
            max_tokens=300,
        )
        if resp:
            title = title or resp.get("title") or article.get("title", "Untitled narrative")[:60]
            thesis = thesis or resp.get("thesis") or article.get("title", "")
        else:
            title = title or article.get("title", "Untitled narrative")[:60]
            thesis = thesis or article.get("content_summary") or article.get("title", "")

    now = datetime.utcnow().isoformat()
    initial_log = [
        {
            "at": now,
            "event": "created",
            "from_article_id": article.get("id"),
        }
    ]
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO narratives
                (title, thesis, status, first_seen, last_evidence_at,
                 evidence_count, evolution_log, created_at, updated_at)
            VALUES (?, ?, 'emerging', ?, ?, 0, ?, ?, ?)
            """,
            (title, thesis, now, now, json.dumps(initial_log), now, now),
        )
        narrative_id = cursor.lastrowid

    # Seed evidence row for the originating article.
    _insert_evidence(
        narrative_id=narrative_id,
        article_id=article["id"],
        contribution="new",
        summary=initial_summary or article.get("title", ""),
    )
    _bump_narrative_after_evidence(narrative_id)

    logger.info(
        f"narrative_created id={narrative_id} title={title!r} from_article={article.get('id')}"
    )
    return narrative_id


def update_narrative(
    narrative_id: int,
    article: dict,
    *,
    contribution: str,
    summary: str,
    llm: Optional[AgentLLM] = None,
) -> bool:
    """Add an evidence row and update the narrative's status/thesis if needed.

    Returns False if the article was already evidence on this narrative
    (idempotent no-op).
    """
    inserted = _insert_evidence(
        narrative_id=narrative_id,
        article_id=article["id"],
        contribution=contribution,
        summary=summary,
    )
    if inserted is None:
        return False

    narrative = get_narrative(narrative_id)
    if narrative is None:
        return False

    # Status auto-promote: emerging -> active after the second piece of evidence.
    new_status = None
    if narrative["status"] == NarrativeStatus.EMERGING.value:
        # narrative.evidence_count is pre-bump; after this update count is +1.
        if narrative["evidence_count"] + 1 >= 2:
            new_status = NarrativeStatus.ACTIVE.value
    elif narrative["status"] in (
        NarrativeStatus.PLATEAUING.value,
        NarrativeStatus.RESOLVED.value,
    ):
        # Fresh evidence revives a plateauing or resolved thread.
        new_status = NarrativeStatus.ACTIVE.value

    # Thesis rewrite: only if contradicts or new_evidence (skip elaborates).
    new_thesis = None
    if contribution in ("contradicts", "new_evidence") and llm is not None:
        try:
            resp = llm.call_json(
                _build_thesis_rewrite_prompt(
                    narrative["thesis"], contribution, summary
                ),
                system=_THESIS_REWRITE_SYSTEM,
                max_tokens=300,
            )
            if resp and resp.get("rewrite") and resp.get("new_thesis"):
                new_thesis = resp["new_thesis"]
        except AgentLLMError as e:
            logger.warning(f"thesis_rewrite_skipped: {e}")

    evolution_entry = {
        "at": datetime.utcnow().isoformat(),
        "event": "evidence_added",
        "contribution": contribution,
        "article_id": article["id"],
        "summary": summary,
        "thesis_rewritten": new_thesis is not None,
        "status_change": new_status,
    }

    _bump_narrative_after_evidence(
        narrative_id,
        new_status=new_status,
        evolution_entry=evolution_entry,
        new_thesis=new_thesis,
    )
    return True


def decay_quiet_narratives(
    *,
    plateau_after_days: int = DEFAULT_STALE_PLATEAU_DAYS,
    resolve_after_days: int = DEFAULT_STALE_RESOLVE_DAYS,
) -> dict[str, int]:
    """Move quiet narratives forward in their lifecycle.

    Active/Emerging with no new evidence in `plateau_after_days` -> plateauing.
    Plateauing or older with no evidence in `resolve_after_days` -> resolved.
    """
    plateau_cutoff = datetime.utcnow() - timedelta(days=plateau_after_days)
    resolve_cutoff = datetime.utcnow() - timedelta(days=resolve_after_days)

    transitions = {"plateaued": 0, "resolved": 0}

    with db_connection() as conn:
        cursor = conn.cursor()

        # Plateau emerging/active that have gone quiet.
        cursor.execute(
            """
            SELECT id FROM narratives
            WHERE status IN ('emerging', 'active')
              AND last_evidence_at < ?
            """,
            (plateau_cutoff.isoformat(),),
        )
        plateaued_ids = [r["id"] for r in cursor.fetchall()]

        # Resolve anything older than resolve_cutoff that's not already resolved.
        cursor.execute(
            """
            SELECT id FROM narratives
            WHERE status != 'resolved'
              AND last_evidence_at < ?
            """,
            (resolve_cutoff.isoformat(),),
        )
        resolved_ids = [r["id"] for r in cursor.fetchall()]

    now = datetime.utcnow().isoformat()
    for nid in plateaued_ids:
        # Don't double-handle: ids that will be resolved should skip plateau.
        if nid in resolved_ids:
            continue
        _bump_narrative_status(nid, NarrativeStatus.PLATEAUING.value)
        transitions["plateaued"] += 1

    for nid in resolved_ids:
        _bump_narrative_status(
            nid, NarrativeStatus.RESOLVED.value,
            resolved_reason=f"No evidence in {resolve_after_days}d",
            resolved_at=now,
        )
        transitions["resolved"] += 1

    logger.info(f"narrative_decay {transitions}")
    return transitions


def _bump_narrative_status(
    narrative_id: int,
    new_status: str,
    *,
    resolved_reason: Optional[str] = None,
    resolved_at: Optional[str] = None,
) -> None:
    with db_connection() as conn:
        cursor = conn.cursor()
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [new_status, datetime.utcnow().isoformat()]
        if resolved_reason is not None:
            sets.append("resolved_reason = ?")
            params.append(resolved_reason)
        if resolved_at is not None:
            sets.append("resolved_at = ?")
            params.append(resolved_at)
        params.append(narrative_id)
        cursor.execute(
            f"UPDATE narratives SET {', '.join(sets)} WHERE id = ?",
            params,
        )


# ---- batch wrapper used by CLI / daemon ---------------------------------


def process_recent_articles(
    articles: list[dict],
    *,
    llm: Optional[AgentLLM] = None,
    create_threshold: float = 0.55,
) -> dict[str, int]:
    """Run match-against-ledger over a batch of articles.

    For each article:
    - Match against the open ledger.
    - If extend with confidence above threshold, append evidence.
    - If new (or extend below threshold), create a new narrative.

    `create_threshold` controls how confident an "extend" must be to take it
    over starting a new narrative.

    Returns counts: created, extended, skipped.
    """
    llm = llm or AgentLLM()
    counts = {"created": 0, "extended": 0, "skipped": 0}

    for article in articles:
        if not article.get("title"):
            counts["skipped"] += 1
            continue

        match = match_against_ledger(article, llm=llm)

        if match.narrative_id is not None and match.confidence >= create_threshold:
            updated = update_narrative(
                match.narrative_id,
                article,
                contribution=match.contribution,
                summary=match.summary,
                llm=llm,
            )
            if updated:
                counts["extended"] += 1
            else:
                # Article was already evidence on this narrative.
                counts["skipped"] += 1
        else:
            create_narrative(
                article,
                llm=llm,
                initial_summary=match.summary,
            )
            counts["created"] += 1

    return counts
