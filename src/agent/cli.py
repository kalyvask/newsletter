"""Click CLI for the agent layer.

Wired into main.py as a subcommand group:

    python main.py agent narratives list
    python main.py agent narratives process [--limit N]
    python main.py agent narratives show <id>
    python main.py agent narratives decay
    python main.py agent leads list
    python main.py agent leads validate [--limit N]
    python main.py agent leads kill-stale [--days 14]
    python main.py agent leads backfill
    python main.py agent status

Each subcommand calls one or two of the discrete capabilities exposed by
`src.agent` and prints the result. No business logic here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from src.agent import (
    auto_dead_stale_leads,
    decay_quiet_narratives,
    init_agent_schema,
    list_narratives,
    list_open_leads,
    process_recent_articles,
    validate_open_leads,
)
from src.agent.leads import backfill_existing_signals_as_leads
from src.agent.llm import AgentLLM, AgentLLMError
from src.agent.narratives import get_narrative, get_narrative_evidence
from src.config import RELEVANCE_THRESHOLD
from src.database import db_connection, get_articles_for_report

logger = logging.getLogger(__name__)
console = Console()


@click.group(name="agent", help="Stateful agent layer: narratives, leads, status.")
def agent_group() -> None:
    """Initialize the agent schema on every invocation. Idempotent."""
    init_agent_schema()


# ---- narratives ---------------------------------------------------------


@agent_group.group(name="narratives", help="Narrative ledger across reports.")
def narratives_group() -> None:
    pass


@narratives_group.command(name="list")
@click.option(
    "--status",
    type=click.Choice(["emerging", "active", "plateauing", "resolved"]),
    default=None,
    help="Filter by narrative status",
)
@click.option("--limit", "-l", default=30, help="Max narratives to show")
def narratives_list(status: Optional[str], limit: int) -> None:
    """List narratives in the ledger."""
    rows = list_narratives(status=status, limit=limit)
    if not rows:
        console.print("[dim]No narratives in the ledger yet.[/]")
        return

    table = Table(title="Narrative ledger")
    table.add_column("ID", style="cyan", width=4)
    table.add_column("Title", width=32)
    table.add_column("Status", width=12)
    table.add_column("#Ev", style="green", justify="right", width=4)
    table.add_column("Last evidence", width=20)
    table.add_column("Thesis", width=60)
    for r in rows:
        last = r.get("last_evidence_at") or ""
        if last and "T" in last:
            last = last.split("T")[0]
        thesis = r.get("thesis") or ""
        if len(thesis) > 60:
            thesis = thesis[:57] + "..."
        table.add_row(
            str(r["id"]),
            (r.get("title") or "")[:32],
            r["status"],
            str(r.get("evidence_count") or 0),
            last,
            thesis,
        )
    console.print(table)


@narratives_group.command(name="show")
@click.argument("narrative_id", type=int)
def narratives_show(narrative_id: int) -> None:
    """Show one narrative with its full evidence trail."""
    narrative = get_narrative(narrative_id)
    if narrative is None:
        console.print(f"[red]No narrative with id {narrative_id}[/]")
        return

    evidence = get_narrative_evidence(narrative_id)

    body = (
        f"**Status:** {narrative['status']}\n"
        f"**Evidence count:** {narrative.get('evidence_count') or 0}\n"
        f"**First seen:** {narrative.get('first_seen', '')}\n"
        f"**Last evidence:** {narrative.get('last_evidence_at', '')}\n\n"
        f"**Thesis:** {narrative['thesis']}\n"
    )
    if narrative.get("resolved_reason"):
        body += f"\n_Resolved: {narrative['resolved_reason']}_\n"

    console.print(Panel(body, title=f"[{narrative_id}] {narrative['title']}", border_style="cyan"))

    if evidence:
        console.print("\n[bold]Evidence:[/]")
        for e in evidence:
            added = (e.get("added_at") or "").split("T")[0]
            console.print(
                f"  - [{added}] [yellow]{e['contribution']}[/]: "
                f"{e.get('article_title', '(no title)')}"
            )
            if e.get("summary"):
                console.print(f"      [dim]{e['summary']}[/]")
            if e.get("article_url"):
                console.print(f"      [dim]{e['article_url']}[/]")


@narratives_group.command(name="process")
@click.option("--limit", "-l", default=20, help="Max recent articles to classify")
@click.option(
    "--days",
    "-d",
    default=7,
    help="Look back this many days for articles",
)
@click.option(
    "--min-relevance",
    default=None,
    type=float,
    help="Override the relevance threshold for articles to consider",
)
def narratives_process(limit: int, days: int, min_relevance: Optional[float]) -> None:
    """Classify recent articles against the ledger; create/extend narratives."""
    threshold = min_relevance if min_relevance is not None else RELEVANCE_THRESHOLD
    since = datetime.utcnow() - timedelta(days=days)
    articles = get_articles_for_report(
        since=since, min_relevance=threshold, limit=limit
    )
    if not articles:
        console.print("[yellow]No relevant articles in window.[/]")
        return

    console.print(
        f"[bold]Processing {len(articles)} articles "
        f"(>= relevance {threshold:.2f}, last {days}d) against the ledger...[/]"
    )

    try:
        llm = AgentLLM()
    except AgentLLMError as e:
        console.print(f"[red]Cannot use Claude: {e}[/]")
        return

    counts = process_recent_articles(articles, llm=llm)
    console.print(
        f"[green]Done.[/] Created: {counts['created']} · "
        f"Extended: {counts['extended']} · Skipped: {counts['skipped']}"
    )


@narratives_group.command(name="decay")
@click.option(
    "--plateau-days",
    default=14,
    help="Days quiet before emerging/active -> plateauing",
)
@click.option(
    "--resolve-days",
    default=35,
    help="Days quiet before any non-resolved -> resolved",
)
def narratives_decay(plateau_days: int, resolve_days: int) -> None:
    """Move quiet narratives forward in their lifecycle."""
    counts = decay_quiet_narratives(
        plateau_after_days=plateau_days,
        resolve_after_days=resolve_days,
    )
    console.print(
        f"[green]Done.[/] Plateaued: {counts['plateaued']} · "
        f"Resolved: {counts['resolved']}"
    )


# ---- leads --------------------------------------------------------------


@agent_group.group(name="leads", help="Tracked-lead validation for opportunity signals.")
def leads_group() -> None:
    pass


@leads_group.command(name="list")
@click.option("--min-strength", default=0.4, type=float, help="Minimum signal_strength")
@click.option("--limit", "-l", default=30, help="Max leads to show")
def leads_list(min_strength: float, limit: int) -> None:
    """List open leads (unconfirmed or corroborated)."""
    leads = list_open_leads(min_strength=min_strength, limit=limit)
    if not leads:
        console.print("[dim]No open leads matching filter.[/]")
        return

    table = Table(title="Open leads")
    table.add_column("ID", style="cyan", width=4)
    table.add_column("Status", width=14)
    table.add_column("Type", width=22)
    table.add_column("Strength", style="green", justify="right", width=8)
    table.add_column("Company", width=22)
    table.add_column("#Corr", justify="right", width=5)
    table.add_column("Summary", width=60)
    for r in leads:
        status = r.get("validation_status") or ""
        company = (r.get("company_name") or "—")[:22]
        summary = (r.get("opportunity_summary") or "")[:58]
        if len(r.get("opportunity_summary") or "") > 58:
            summary = summary + ".."
        table.add_row(
            str(r["id"]),
            status,
            (r.get("signal_type") or "")[:22],
            f"{(r.get('signal_strength') or 0):.2f}",
            company,
            str(r.get("corroboration_count") or 0),
            summary,
        )
    console.print(table)


@leads_group.command(name="validate")
@click.option("--limit", "-l", default=100, help="Max recent articles to check against")
@click.option(
    "--days",
    "-d",
    default=7,
    help="Look back this many days for new evidence",
)
def leads_validate(limit: int, days: int) -> None:
    """Check open leads for corroboration in recent articles."""
    since = datetime.utcnow() - timedelta(days=days)
    articles = get_articles_for_report(since=since, min_relevance=0.4, limit=limit)
    if not articles:
        console.print("[yellow]No recent articles to check against.[/]")
        return

    console.print(
        f"[bold]Validating leads against {len(articles)} articles from last {days}d...[/]"
    )

    try:
        llm = AgentLLM()
    except AgentLLMError as e:
        console.print(f"[red]Cannot use Claude: {e}[/]")
        return

    counts = validate_open_leads(articles, llm=llm)
    console.print(
        f"[green]Done.[/] "
        f"Leads checked: {counts['leads_checked']} · "
        f"Articles checked: {counts['articles_checked']} · "
        f"Corroborations added: {counts['corroborations_added']} · "
        f"Promoted to corroborated: {counts['leads_promoted']}"
    )


@leads_group.command(name="kill-stale")
@click.option(
    "--days",
    default=14,
    help="Mark unconfirmed leads dead after this many quiet days",
)
def leads_kill_stale(days: int) -> None:
    """Mark uncorroborated leads dead after going quiet."""
    n = auto_dead_stale_leads(stale_days=days)
    console.print(f"[green]Done.[/] Marked {n} stale leads as dead.")


@leads_group.command(name="backfill")
def leads_backfill() -> None:
    """One-shot: register every existing opportunity_signals row as a lead."""
    n = backfill_existing_signals_as_leads()
    console.print(f"[green]Done.[/] Registered {n} pre-existing signals as leads.")


# ---- status (unified summary) -------------------------------------------


@agent_group.command(name="status")
def agent_status() -> None:
    """Pipeline snapshot: narratives by status + leads by status."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, COUNT(*) as n FROM narratives GROUP BY status"
        )
        narrative_counts = {r["status"]: r["n"] for r in cursor.fetchall()}

        cursor.execute(
            """
            SELECT validation_status, COUNT(*) as n
            FROM opportunity_signals
            WHERE validation_status IS NOT NULL
            GROUP BY validation_status
            """
        )
        lead_counts = {r["validation_status"]: r["n"] for r in cursor.fetchall()}

    if not narrative_counts and not lead_counts:
        console.print(
            "[dim]Agent state is empty. "
            "Run [bold]python main.py agent narratives process[/] "
            "or [bold]python main.py agent leads backfill[/] to start.[/]"
        )
        return

    if narrative_counts:
        ntable = Table(title="Narratives by status")
        ntable.add_column("Status", style="cyan")
        ntable.add_column("Count", style="green", justify="right")
        for status_name in ("emerging", "active", "plateauing", "resolved"):
            if status_name in narrative_counts:
                ntable.add_row(status_name, str(narrative_counts[status_name]))
        console.print(ntable)

    if lead_counts:
        ltable = Table(title="Leads by validation status")
        ltable.add_column("Status", style="cyan")
        ltable.add_column("Count", style="green", justify="right")
        for status_name in ("unconfirmed", "corroborated", "dead"):
            if status_name in lead_counts:
                ltable.add_row(status_name, str(lead_counts[status_name]))
        console.print(ltable)
