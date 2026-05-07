"""Stateful agent layer over the batch pipeline.

The batch pipeline (scrape -> analyze -> report) is stateless: each run treats
articles in isolation, so repeated trends get rediscovered, opportunity
signals fire and vanish, and reports have no memory of last week's themes.

This package adds two narrow capabilities on top:

- `narratives`: a ledger of active narratives across reports. New articles
  are matched against active narratives; matches add evidence to the
  existing thread, mismatches start a new one, and quiet narratives decay
  to plateauing/resolved over time.

- `leads`: tracked-lead validation for the opportunity signals already
  detected by `processors/analyzer.py`. Open leads are re-checked against
  fresh articles for corroboration; uncorroborated leads age out and die.

Each capability is a discrete function (not a god-loop) so the same code
serves the interactive CLI and a future autonomous daemon.
"""

from src.agent.narratives import (
    NarrativeMatch,
    NarrativeStatus,
    create_narrative,
    decay_quiet_narratives,
    list_narratives,
    match_against_ledger,
    process_recent_articles,
    update_narrative,
)
from src.agent.leads import (
    LeadStatus,
    auto_dead_stale_leads,
    list_open_leads,
    register_signal_as_lead,
    validate_open_leads,
)
from src.agent.schema import init_agent_schema

__all__ = [
    "NarrativeMatch",
    "NarrativeStatus",
    "LeadStatus",
    "create_narrative",
    "decay_quiet_narratives",
    "list_narratives",
    "match_against_ledger",
    "process_recent_articles",
    "update_narrative",
    "auto_dead_stale_leads",
    "list_open_leads",
    "register_signal_as_lead",
    "validate_open_leads",
    "init_agent_schema",
]
