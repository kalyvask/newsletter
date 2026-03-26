#!/usr/bin/env python3
"""
AI Deployment Research Monitor - CLI Entry Point

A tool for monitoring and analyzing content about AI deployment strategies,
implementation models, and enterprise AI adoption.
"""

import sys
import time
import logging
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.markdown import Markdown

# Import our modules
from src.config import DATABASE_PATH, RELEVANCE_THRESHOLD
from src.database import (
    init_database,
    seed_sources,
    get_source_by_type,
    get_unprocessed_articles,
    get_recent_articles,
    search_articles,
    insert_article,
    update_article_analysis,
    update_source_last_checked,
    get_database_stats,
)
from src.scrapers import RedditScraper, HackerNewsScraper, RSSFeedScraper
from src.processors import RelevanceScorer, ContentAnalyzer
from src.reports import ReportGenerator
from src.emailer import send_newsletter
from src.utils import setup_logging

console = Console()
logger = logging.getLogger(__name__)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
def cli(verbose):
    """AI Deployment Research Monitor - Track AI implementation trends."""
    level = "DEBUG" if verbose else "INFO"
    setup_logging(level)


@cli.command()
def init():
    """Initialize the database and seed sources."""
    console.print("[bold blue]Initializing AI Deployment Research Monitor...[/]")

    with console.status("Creating database..."):
        init_database()
        console.print(f"[green]✓[/] Database created at {DATABASE_PATH}")

    with console.status("Seeding sources..."):
        seed_sources()
        console.print("[green]✓[/] Sources seeded")

    stats = get_database_stats()
    console.print(
        f"\n[bold]Ready![/] Database has {stats['active_sources']} active sources."
    )
    console.print("\nRun [bold cyan]python main.py scrape[/] to start collecting data.")


@cli.command()
@click.option(
    "--source",
    "-s",
    type=click.Choice(["reddit", "hackernews", "rss", "all"]),
    default="all",
    help="Source to scrape",
)
@click.option("--limit", "-l", default=50, help="Max items per source")
def scrape(source, limit):
    """Scrape content from configured sources."""
    # Ensure database exists
    init_database()

    scorer = RelevanceScorer()
    total_new = 0

    sources_to_scrape = []
    if source in ["reddit", "all"]:
        sources_to_scrape.append(("reddit", RedditScraper()))
    if source in ["hackernews", "all"]:
        sources_to_scrape.append(("hackernews", HackerNewsScraper(items_to_fetch=limit)))
    if source in ["rss", "all"]:
        sources_to_scrape.append(("rss", RSSFeedScraper(max_entries_per_feed=limit // 5)))

    for source_type, scraper in sources_to_scrape:
        console.print(f"\n[bold cyan]Scraping {source_type}...[/]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Fetching from {source_type}...", total=None)

            try:
                items = scraper.run()
                progress.update(task, description=f"Found {len(items)} items")

                # Get source IDs
                db_sources = get_source_by_type(source_type)
                source_id_map = {s["name"]: s["id"] for s in db_sources}

                new_count = 0
                relevant_count = 0

                for item in items:
                    # Score relevance
                    relevance = scorer.score(
                        item.get("title", ""),
                        item.get("content", ""),
                    )

                    # Quick filter - skip clearly irrelevant items
                    if relevance["score"] < 0.3:
                        continue

                    # Determine source ID
                    source_name = item.get("source_name", source_type)
                    source_id = source_id_map.get(
                        source_name,
                        list(source_id_map.values())[0] if source_id_map else 1,
                    )

                    # Insert article
                    article_id = insert_article(
                        source_id=source_id,
                        title=item["title"],
                        url=item["url"],
                        author=item.get("author"),
                        published_date=item.get("published_date"),
                        content=item.get("content", ""),
                        relevance_score=relevance["score"],
                        keywords_matched=relevance["all_keywords_matched"],
                    )

                    if article_id:
                        new_count += 1
                        if relevance["is_relevant"]:
                            relevant_count += 1

                # Update last checked time
                for db_source in db_sources:
                    update_source_last_checked(db_source["id"])

                progress.update(
                    task,
                    description=f"[green]✓[/] {new_count} new, {relevant_count} relevant",
                )
                total_new += new_count

            except Exception as e:
                progress.update(task, description=f"[red]✗[/] Error: {e}")
                logger.exception(f"Error scraping {source_type}")

    console.print(f"\n[bold green]Done![/] Added {total_new} new articles.")


@cli.command()
@click.option("--limit", "-l", default=50, help="Max articles to analyze")
@click.option("--skip-api", is_flag=True, help="Skip Claude API analysis")
def analyze(limit, skip_api):
    """Analyze unprocessed articles using Claude API."""
    init_database()

    articles = get_unprocessed_articles(limit=limit)
    if not articles:
        console.print("[yellow]No unprocessed articles to analyze.[/]")
        return

    console.print(f"[bold]Analyzing {len(articles)} articles...[/]\n")

    scorer = RelevanceScorer()
    analyzer = ContentAnalyzer() if not skip_api else None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing...", total=len(articles))

        for article in articles:
            title = article["title"][:50] + "..." if len(article["title"]) > 50 else article["title"]
            progress.update(task, description=f"Analyzing: {title}")

            # Re-score with full content
            relevance = scorer.score(
                article["title"],
                article.get("full_content", ""),
            )

            # Get Claude analysis if available and article is relevant
            summary = ""
            sentiment = "neutral"
            category = "news"

            if analyzer and relevance["score"] >= RELEVANCE_THRESHOLD:
                analysis = analyzer.analyze(
                    article["title"],
                    article.get("full_content", ""),
                    article.get("url", ""),
                )
                if analysis:
                    summary = analysis.get("summary", "")
                    sentiment = analysis.get("sentiment", "neutral")
                    category = analysis.get("category", "news")

            # Update article
            update_article_analysis(
                article_id=article["id"],
                summary=summary,
                sentiment=sentiment,
                category=category,
                relevance_score=relevance["score"],
                keywords_matched=relevance["all_keywords_matched"],
            )

            progress.advance(task)
            time.sleep(0.1)  # Small delay between API calls

    if analyzer:
        console.print(f"\n[dim]Claude API tokens used: {analyzer.get_token_usage()}[/]")

    console.print("[bold green]Analysis complete![/]")


@cli.command()
@click.option(
    "--type",
    "-t",
    "report_type",
    type=click.Choice(["weekly", "daily", "executive"]),
    default="executive",
    help="Report type (executive = 1-2 page written briefing)",
)
@click.option("--output", "-o", help="Output filename")
@click.option("--print", "print_report", is_flag=True, help="Print to console")
def report(report_type, output, print_report):
    """Generate a research report."""
    init_database()

    generator = ReportGenerator()

    console.print(f"[bold]Generating {report_type} report...[/]\n")

    if report_type == "executive":
        content = generator.generate_executive_briefing()
    elif report_type == "weekly":
        content = generator.generate_weekly_report()
    else:
        content = generator.generate_daily_digest()

    if print_report:
        console.print(Markdown(content))
    else:
        filepath = generator.save_report(content, report_type, output)
        console.print(f"[green]✓[/] Report saved to: {filepath}")


@cli.command()
@click.option("--limit", "-l", default=20, help="Number of articles to show")
@click.option(
    "--sort",
    "-s",
    type=click.Choice(["relevance", "discovered"]),
    default="relevance",
    help="Sort order",
)
def view(limit, sort):
    """View recent articles."""
    init_database()

    articles = get_recent_articles(limit=limit, sort_by=sort)

    if not articles:
        console.print("[yellow]No articles found.[/]")
        return

    table = Table(title="Recent Articles", show_lines=True)
    table.add_column("Score", style="cyan", width=6)
    table.add_column("Title", style="white", max_width=50)
    table.add_column("Source", style="green", width=15)
    table.add_column("Category", style="yellow", width=12)

    for article in articles:
        score = f"{article.get('relevance_score', 0):.2f}"
        title = article.get("title", "")[:50]
        source = article.get("source_name", "")[:15]
        category = article.get("category", "")[:12]

        table.add_row(score, title, source, category)

    console.print(table)


@cli.command()
@click.argument("query")
@click.option("--limit", "-l", default=20, help="Max results")
def search(query, limit):
    """Search articles by keyword."""
    init_database()

    articles = search_articles(query, limit=limit)

    if not articles:
        console.print(f"[yellow]No articles found matching '{query}'[/]")
        return

    console.print(f"[bold]Found {len(articles)} articles matching '{query}':[/]\n")

    for article in articles:
        score = article.get("relevance_score", 0)
        title = article.get("title", "")
        url = article.get("url", "")
        source = article.get("source_name", "")

        console.print(f"[cyan]{score:.2f}[/] [{source}] {title}")
        console.print(f"      [dim]{url}[/]\n")


@cli.command()
def stats():
    """Show database statistics."""
    init_database()

    stats = get_database_stats()

    panel_content = f"""
[bold cyan]Database Statistics[/]

📊 [bold]Content:[/]
   • Total articles: {stats['total_articles']}
   • Processed: {stats['processed_articles']}
   • High relevance: {stats['relevant_articles']}
   • Insights extracted: {stats['total_insights']}

📰 [bold]Sources:[/]
   • Active sources: {stats['active_sources']}

📝 [bold]Reports:[/]
   • Generated: {stats['total_reports']}
"""

    console.print(Panel(panel_content, title="AI Deployment Monitor"))


@cli.command()
@click.option("--interval", "-i", default="6h", help="Scrape interval (e.g., 6h, 30m)")
@click.option("--send-email", is_flag=True, help="Send newsletter email after each run")
@click.option(
    "--email-type",
    type=click.Choice(["daily", "weekly", "executive"]),
    default="daily",
    help="Report type for email",
)
def daemon(interval, send_email, email_type):
    """Run in daemon mode with scheduled scraping."""
    import schedule
    from functools import partial

    pipeline = partial(run_pipeline, send_email=send_email, report_type=email_type)

    # Parse interval
    if interval.endswith("h"):
        hours = int(interval[:-1])
        schedule.every(hours).hours.do(pipeline)
    elif interval.endswith("m"):
        minutes = int(interval[:-1])
        schedule.every(minutes).minutes.do(pipeline)
    else:
        console.print("[red]Invalid interval format. Use format like '6h' or '30m'[/]")
        return

    mode = "with email" if send_email else "without email"
    console.print(f"[bold green]Daemon started![/] Scraping every {interval} ({mode})")
    console.print("Press Ctrl+C to stop.\n")

    # Run once immediately
    pipeline()

    # Then run on schedule
    try:
        while True:
            schedule.run_pending()
            time.sleep(60)
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped.[/]")


def run_pipeline(send_email: bool = False, report_type: str = "daily"):
    """Run the full scrape -> analyze -> (optionally email) pipeline."""
    console.print(f"\n[bold]Running pipeline at {datetime.now().strftime('%Y-%m-%d %H:%M')}[/]")

    # Scrape
    from click.testing import CliRunner

    runner = CliRunner()

    result = runner.invoke(scrape, ["--source", "all"])
    if result.output:
        console.print(result.output)

    # Analyze
    result = runner.invoke(analyze, ["--limit", "30"])
    if result.output:
        console.print(result.output)

    # Optionally generate & send newsletter
    if send_email:
        generator = ReportGenerator()
        if report_type == "executive":
            content = generator.generate_executive_briefing()
        elif report_type == "weekly":
            content = generator.generate_weekly_report()
        else:
            content = generator.generate_daily_digest()
        generator.save_report(content, report_type)
        success = send_newsletter(content, report_type)
        if success:
            console.print("[green]✓ Newsletter sent.[/]")
        else:
            console.print("[red]✗ Newsletter send failed.[/]")

    console.print("[green]Pipeline complete.[/]")


@cli.command()
@click.option("--format", "-f", "fmt", type=click.Choice(["csv", "json"]), default="csv")
@click.option("--output", "-o", required=True, help="Output file path")
@click.option("--limit", "-l", default=1000, help="Max records")
def export(fmt, output, limit):
    """Export data to CSV or JSON."""
    import csv
    import json

    init_database()

    articles = get_recent_articles(limit=limit, sort_by="discovered")

    if not articles:
        console.print("[yellow]No articles to export.[/]")
        return

    if fmt == "csv":
        with open(output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "title",
                    "url",
                    "source_name",
                    "relevance_score",
                    "category",
                    "sentiment",
                    "content_summary",
                    "discovered_date",
                ],
            )
            writer.writeheader()
            for article in articles:
                writer.writerow({
                    "title": article.get("title", ""),
                    "url": article.get("url", ""),
                    "source_name": article.get("source_name", ""),
                    "relevance_score": article.get("relevance_score", 0),
                    "category": article.get("category", ""),
                    "sentiment": article.get("sentiment", ""),
                    "content_summary": article.get("content_summary", ""),
                    "discovered_date": article.get("discovered_date", ""),
                })
    else:
        with open(output, "w", encoding="utf-8") as f:
            json.dump(articles, f, indent=2, default=str)

    console.print(f"[green]✓[/] Exported {len(articles)} articles to {output}")


@cli.command()
@click.option(
    "--type",
    "-t",
    "report_type",
    type=click.Choice(["weekly", "daily", "executive"]),
    default="executive",
    help="Report type to send",
)
@click.option("--to", "recipient", default=None, help="Override recipient email")
@click.option("--dry-run", is_flag=True, help="Generate report but don't send email")
def newsletter(report_type, recipient, dry_run):
    """Run full pipeline and send newsletter email."""
    init_database()
    seed_sources()

    # Step 1: Scrape
    console.print("\n[bold cyan]Step 1: Scraping sources...[/]")
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(scrape, ["--source", "all"])
    if result.output:
        console.print(result.output)

    # Step 2: Analyze
    console.print("\n[bold cyan]Step 2: Analyzing articles...[/]")
    result = runner.invoke(analyze, ["--limit", "30"])
    if result.output:
        console.print(result.output)

    # Step 3: Generate report
    console.print(f"\n[bold cyan]Step 3: Generating {report_type} report...[/]")
    generator = ReportGenerator()

    if report_type == "executive":
        content = generator.generate_executive_briefing()
    elif report_type == "weekly":
        content = generator.generate_weekly_report()
    else:
        content = generator.generate_daily_digest()

    # Save report to file
    filepath = generator.save_report(content, report_type)
    console.print(f"[green]✓[/] Report saved to: {filepath}")

    # Step 4: Send email
    if dry_run:
        console.print("\n[yellow]Dry run — skipping email send.[/]")
        console.print("[dim]Report preview (first 500 chars):[/]")
        console.print(Panel(content[:500] + "...", title="Report Preview"))
        return

    console.print("\n[bold cyan]Step 4: Sending newsletter...[/]")
    success = send_newsletter(content, report_type, recipient)

    if success:
        console.print("[bold green]✓ Newsletter sent successfully![/]")
    else:
        console.print("[bold red]✗ Failed to send newsletter. Check SMTP settings in .env[/]")


@cli.command()
def run():
    """Run the full pipeline: scrape, analyze, and report."""
    console.print(Panel(
        "[bold]AI Deployment Research Monitor[/]\n\n"
        "Running full pipeline: scrape → analyze → report",
        title="Starting",
    ))

    init_database()
    seed_sources()

    # Scrape all sources
    console.print("\n[bold cyan]Step 1: Scraping sources...[/]")
    from click.testing import CliRunner

    runner = CliRunner()
    result = runner.invoke(scrape, ["--source", "all"])
    console.print(result.output)

    # Analyze articles
    console.print("\n[bold cyan]Step 2: Analyzing articles...[/]")
    result = runner.invoke(analyze, ["--limit", "50"])
    console.print(result.output)

    # Generate report
    console.print("\n[bold cyan]Step 3: Generating report...[/]")
    result = runner.invoke(report, ["--type", "weekly"])
    console.print(result.output)

    # Show stats
    console.print("\n[bold cyan]Summary:[/]")
    result = runner.invoke(stats)
    console.print(result.output)

    console.print("[bold green]Pipeline complete![/]")


if __name__ == "__main__":
    cli()
