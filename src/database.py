"""Database operations for the AI Deployment Research Monitor."""

import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

from .config import DATABASE_PATH


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def db_connection():
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Initialize the database with required tables."""
    with db_connection() as conn:
        cursor = conn.cursor()

        # Sources table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                url TEXT,
                last_checked TIMESTAMP,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Articles table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER,
                title TEXT NOT NULL,
                url TEXT UNIQUE,
                author TEXT,
                published_date TIMESTAMP,
                discovered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                content_summary TEXT,
                full_content TEXT,
                relevance_score FLOAT DEFAULT 0.0,
                keywords_matched TEXT,
                sentiment TEXT,
                category TEXT,
                is_processed BOOLEAN DEFAULT FALSE,
                is_included_in_report BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (source_id) REFERENCES sources(id)
            )
        """)

        # Insights table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS insights (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                insight_text TEXT NOT NULL,
                insight_type TEXT,
                companies_mentioned TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            )
        """)

        # Reports table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                report_type TEXT NOT NULL,
                title TEXT,
                content TEXT,
                articles_included TEXT,
                stats TEXT
            )
        """)

        # Opportunity signals table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS opportunity_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER,
                signal_type TEXT NOT NULL,
                signal_strength FLOAT DEFAULT 0.0,
                company_name TEXT,
                industry_vertical TEXT,
                deployment_stage TEXT,
                business_impact TEXT,
                opportunity_summary TEXT,
                keywords_triggered TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            )
        """)

        # Company intelligence table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS company_intel (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT UNIQUE NOT NULL,
                industry TEXT,
                size_tier TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_mentions INTEGER DEFAULT 1,
                latest_signal_type TEXT,
                latest_deployment_stage TEXT,
                funding_signals TEXT,
                technologies_used TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add new columns for opportunity tracking (safe migration)
        for col_def in [
            "ALTER TABLE articles ADD COLUMN opportunity_score FLOAT DEFAULT 0.0",
            "ALTER TABLE articles ADD COLUMN opportunity_types TEXT",
            "ALTER TABLE articles ADD COLUMN industry_vertical TEXT",
            "ALTER TABLE articles ADD COLUMN deployment_stage TEXT",
        ]:
            try:
                cursor.execute(col_def)
            except Exception:
                pass  # Column already exists


        # Create indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_relevance ON articles(relevance_score DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_discovered ON articles(discovered_date DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_processed ON articles(is_processed)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_oppsig_type ON opportunity_signals(signal_type)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_oppsig_strength ON opportunity_signals(signal_strength DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_company_intel_name ON company_intel(company_name)
        """)


def seed_sources():
    """Seed initial sources into the database."""
    from .config import REDDIT_SUBREDDITS, RSS_FEEDS

    sources = []

    # Add all Reddit subreddits from config
    for subreddit in REDDIT_SUBREDDITS:
        sources.append((f"r/{subreddit}", "reddit", f"https://reddit.com/r/{subreddit}"))

    # Add Hacker News
    sources.append(("Hacker News", "hackernews", "https://news.ycombinator.com"))

    # Add all RSS feeds from config - use the config keys as source names
    # The RSS scraper will use these same keys
    for feed_key, feed_url in RSS_FEEDS.items():
        sources.append((feed_key, "rss", feed_url))

    with db_connection() as conn:
        cursor = conn.cursor()
        for name, source_type, url in sources:
            cursor.execute("""
                INSERT OR IGNORE INTO sources (name, type, url)
                VALUES (?, ?, ?)
            """, (name, source_type, url))


# Source operations
def get_source_by_name(name: str) -> Optional[Dict]:
    """Get a source by name."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sources WHERE name = ?", (name,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_source_by_type(source_type: str) -> List[Dict]:
    """Get all sources of a given type."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM sources WHERE type = ? AND is_active = TRUE",
            (source_type,)
        )
        return [dict(row) for row in cursor.fetchall()]


def update_source_last_checked(source_id: int):
    """Update the last_checked timestamp for a source."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE sources SET last_checked = ? WHERE id = ?",
            (datetime.utcnow(), source_id)
        )


# Article operations
def article_exists(url: str) -> bool:
    """Check if an article with the given URL already exists."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM articles WHERE url = ?", (url,))
        return cursor.fetchone() is not None


def insert_article(
    source_id: int,
    title: str,
    url: str,
    author: Optional[str] = None,
    published_date: Optional[datetime] = None,
    content: Optional[str] = None,
    relevance_score: float = 0.0,
    keywords_matched: Optional[List[str]] = None,
) -> Optional[int]:
    """Insert a new article into the database."""
    if article_exists(url):
        return None

    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO articles (
                source_id, title, url, author, published_date,
                full_content, relevance_score, keywords_matched
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            source_id,
            title,
            url,
            author,
            published_date,
            content,
            relevance_score,
            json.dumps(keywords_matched) if keywords_matched else None,
        ))
        return cursor.lastrowid


def update_article_analysis(
    article_id: int,
    summary: str,
    sentiment: str,
    category: str,
    relevance_score: float,
    keywords_matched: List[str],
):
    """Update an article with analysis results."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE articles SET
                content_summary = ?,
                sentiment = ?,
                category = ?,
                relevance_score = ?,
                keywords_matched = ?,
                is_processed = TRUE
            WHERE id = ?
        """, (
            summary,
            sentiment,
            category,
            relevance_score,
            json.dumps(keywords_matched),
            article_id,
        ))


def get_unprocessed_articles(limit: int = 50) -> List[Dict]:
    """Get articles that haven't been processed yet."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, s.name as source_name, s.type as source_type
            FROM articles a
            JOIN sources s ON a.source_id = s.id
            WHERE a.is_processed = FALSE
            ORDER BY a.discovered_date DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_articles_for_report(
    since: datetime,
    min_relevance: float = 0.6,
    limit: int = 100,
    include_unprocessed: bool = True,
) -> List[Dict]:
    """Get articles for report generation."""
    with db_connection() as conn:
        cursor = conn.cursor()
        if include_unprocessed:
            # Include all articles regardless of processing status
            cursor.execute("""
                SELECT a.*, s.name as source_name, s.type as source_type
                FROM articles a
                JOIN sources s ON a.source_id = s.id
                WHERE a.relevance_score >= ?
                  AND a.discovered_date >= ?
                ORDER BY a.relevance_score DESC, a.discovered_date DESC
                LIMIT ?
            """, (min_relevance, since, limit))
        else:
            # Only processed articles
            cursor.execute("""
                SELECT a.*, s.name as source_name, s.type as source_type
                FROM articles a
                JOIN sources s ON a.source_id = s.id
                WHERE a.is_processed = TRUE
                  AND a.relevance_score >= ?
                  AND a.discovered_date >= ?
                ORDER BY a.relevance_score DESC, a.discovered_date DESC
                LIMIT ?
            """, (min_relevance, since, limit))
        return [dict(row) for row in cursor.fetchall()]


def get_recent_articles(limit: int = 20, sort_by: str = "discovered", include_unprocessed: bool = True) -> List[Dict]:
    """Get recent articles with optional sorting."""
    order_col = "discovered_date" if sort_by == "discovered" else "relevance_score"
    with db_connection() as conn:
        cursor = conn.cursor()
        if include_unprocessed:
            cursor.execute(f"""
                SELECT a.*, s.name as source_name, s.type as source_type
                FROM articles a
                JOIN sources s ON a.source_id = s.id
                ORDER BY a.{order_col} DESC
                LIMIT ?
            """, (limit,))
        else:
            cursor.execute(f"""
                SELECT a.*, s.name as source_name, s.type as source_type
                FROM articles a
                JOIN sources s ON a.source_id = s.id
                WHERE a.is_processed = TRUE
                ORDER BY a.{order_col} DESC
                LIMIT ?
            """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def search_articles(query: str, limit: int = 50) -> List[Dict]:
    """Search articles by title or content."""
    search_term = f"%{query}%"
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.*, s.name as source_name, s.type as source_type
            FROM articles a
            JOIN sources s ON a.source_id = s.id
            WHERE a.title LIKE ? OR a.content_summary LIKE ? OR a.full_content LIKE ?
            ORDER BY a.relevance_score DESC, a.discovered_date DESC
            LIMIT ?
        """, (search_term, search_term, search_term, limit))
        return [dict(row) for row in cursor.fetchall()]


# Insight operations
def insert_insight(
    article_id: int,
    insight_text: str,
    insight_type: str,
    companies_mentioned: Optional[List[str]] = None,
) -> int:
    """Insert a new insight."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO insights (article_id, insight_text, insight_type, companies_mentioned)
            VALUES (?, ?, ?, ?)
        """, (
            article_id,
            insight_text,
            insight_type,
            json.dumps(companies_mentioned) if companies_mentioned else None,
        ))
        return cursor.lastrowid


def get_insights_for_article(article_id: int) -> List[Dict]:
    """Get all insights for an article."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM insights WHERE article_id = ?",
            (article_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_recent_insights(limit: int = 50) -> List[Dict]:
    """Get recent insights with article info."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT i.*, a.title as article_title, a.url as article_url
            FROM insights i
            JOIN articles a ON i.article_id = a.id
            ORDER BY i.created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


# Report operations
def insert_report(
    report_type: str,
    title: str,
    content: str,
    articles_included: List[int],
    stats: Dict[str, Any],
) -> int:
    """Insert a new report."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO reports (report_type, title, content, articles_included, stats)
            VALUES (?, ?, ?, ?, ?)
        """, (
            report_type,
            title,
            content,
            json.dumps(articles_included),
            json.dumps(stats),
        ))
        return cursor.lastrowid


def get_latest_report(report_type: str = "weekly") -> Optional[Dict]:
    """Get the most recent report of a given type."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM reports
            WHERE report_type = ?
            ORDER BY generated_date DESC
            LIMIT 1
        """, (report_type,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_reports(limit: int = 20, exclude_types: List[str] = None) -> List[Dict]:
    """Get all reports, newest first. Optionally exclude certain report types."""
    with db_connection() as conn:
        cursor = conn.cursor()
        if exclude_types:
            placeholders = ",".join("?" * len(exclude_types))
            cursor.execute(f"""
                SELECT id, report_type, title, content, generated_date
                FROM reports
                WHERE report_type NOT IN ({placeholders})
                ORDER BY generated_date DESC
                LIMIT ?
            """, (*exclude_types, limit))
        else:
            cursor.execute("""
                SELECT id, report_type, title, content, generated_date
                FROM reports
                ORDER BY generated_date DESC
                LIMIT ?
            """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


# Opportunity signal operations
def insert_opportunity_signal(
    article_id: int,
    signal_type: str,
    signal_strength: float = 0.0,
    company_name: str = None,
    industry_vertical: str = None,
    deployment_stage: str = None,
    business_impact: str = None,
    opportunity_summary: str = None,
    keywords_triggered: List[str] = None,
) -> int:
    """Insert a new opportunity signal."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO opportunity_signals (
                article_id, signal_type, signal_strength, company_name,
                industry_vertical, deployment_stage, business_impact,
                opportunity_summary, keywords_triggered
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            article_id, signal_type, signal_strength, company_name,
            industry_vertical, deployment_stage, business_impact,
            opportunity_summary,
            json.dumps(keywords_triggered) if keywords_triggered else None,
        ))
        return cursor.lastrowid


def get_recent_opportunity_signals(limit: int = 20) -> List[Dict]:
    """Get recent opportunity signals with article info."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT os.*, a.title as article_title, a.url as article_url,
                   a.source_id, s.name as source_name
            FROM opportunity_signals os
            JOIN articles a ON os.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            ORDER BY os.signal_strength DESC, os.created_at DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def get_opportunity_stats() -> Dict[str, Any]:
    """Get opportunity signal statistics."""
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM opportunity_signals")
        total_signals = cursor.fetchone()[0]

        cursor.execute(
            "SELECT signal_type, COUNT(*) as cnt FROM opportunity_signals GROUP BY signal_type ORDER BY cnt DESC"
        )
        signal_types = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(
            "SELECT industry_vertical, COUNT(*) as cnt FROM opportunity_signals WHERE industry_vertical IS NOT NULL GROUP BY industry_vertical ORDER BY cnt DESC"
        )
        verticals = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute(
            "SELECT company_name, COUNT(*) as cnt FROM opportunity_signals WHERE company_name IS NOT NULL GROUP BY company_name ORDER BY cnt DESC LIMIT 10"
        )
        top_companies = {row[0]: row[1] for row in cursor.fetchall()}

        return {
            "total_signals": total_signals,
            "signal_types": signal_types,
            "verticals": verticals,
            "top_companies": top_companies,
        }


# Company intelligence operations
def upsert_company_intel(
    company_name: str,
    industry: str = None,
    size_tier: str = None,
    signal_type: str = None,
    deployment_stage: str = None,
    technologies: List[str] = None,
):
    """Insert or update company intelligence."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, total_mentions FROM company_intel WHERE company_name = ?", (company_name,))
        existing = cursor.fetchone()

        if existing:
            updates = ["total_mentions = total_mentions + 1", "last_seen = CURRENT_TIMESTAMP", "updated_at = CURRENT_TIMESTAMP"]
            params = []
            if industry:
                updates.append("industry = ?")
                params.append(industry)
            if signal_type:
                updates.append("latest_signal_type = ?")
                params.append(signal_type)
            if deployment_stage:
                updates.append("latest_deployment_stage = ?")
                params.append(deployment_stage)
            if technologies:
                updates.append("technologies_used = ?")
                params.append(json.dumps(technologies))
            params.append(company_name)
            cursor.execute(f"UPDATE company_intel SET {', '.join(updates)} WHERE company_name = ?", params)
        else:
            cursor.execute("""
                INSERT INTO company_intel (company_name, industry, size_tier, latest_signal_type, latest_deployment_stage, technologies_used)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (company_name, industry, size_tier, signal_type, deployment_stage, json.dumps(technologies) if technologies else None))


def get_company_intel(limit: int = 20) -> List[Dict]:
    """Get company intelligence sorted by recent activity."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM company_intel
            ORDER BY total_mentions DESC, last_seen DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]


def update_article_opportunity(
    article_id: int,
    opportunity_score: float,
    opportunity_types: List[str],
    industry_vertical: str = None,
    deployment_stage: str = None,
):
    """Update an article with opportunity analysis results."""
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE articles SET
                opportunity_score = ?,
                opportunity_types = ?,
                industry_vertical = ?,
                deployment_stage = ?
            WHERE id = ?
        """, (
            opportunity_score,
            json.dumps(opportunity_types) if opportunity_types else None,
            industry_vertical,
            deployment_stage,
            article_id,
        ))


# Statistics
def get_database_stats() -> Dict[str, Any]:
    """Get database statistics."""
    with db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM sources WHERE is_active = TRUE")
        active_sources = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM articles")
        total_articles = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM articles WHERE is_processed = TRUE")
        processed_articles = cursor.fetchone()[0]

        cursor.execute(
            "SELECT COUNT(*) FROM articles WHERE relevance_score >= 0.6"
        )
        relevant_articles = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM insights")
        total_insights = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports")
        total_reports = cursor.fetchone()[0]

        try:
            cursor.execute("SELECT COUNT(*) FROM opportunity_signals")
            total_opportunity_signals = cursor.fetchone()[0]
        except Exception:
            total_opportunity_signals = 0

        try:
            cursor.execute("SELECT COUNT(*) FROM company_intel")
            total_companies_tracked = cursor.fetchone()[0]
        except Exception:
            total_companies_tracked = 0

        return {
            "active_sources": active_sources,
            "total_articles": total_articles,
            "processed_articles": processed_articles,
            "relevant_articles": relevant_articles,
            "total_insights": total_insights,
            "total_reports": total_reports,
            "total_opportunity_signals": total_opportunity_signals,
            "total_companies_tracked": total_companies_tracked,
        }


def cleanup_old_articles(max_age_days: int = 60) -> Dict[str, int]:
    """
    Remove articles older than max_age_days (default: 2 months).
    Also removes related opportunity signals and insights.
    Returns counts of deleted records.
    """
    cutoff = (datetime.utcnow() - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")

    with db_connection() as conn:
        cursor = conn.cursor()

        # Get IDs of old articles for cascading deletes
        cursor.execute(
            "SELECT id FROM articles WHERE discovered_date < ?", (cutoff,)
        )
        old_ids = [row[0] for row in cursor.fetchall()]

        if not old_ids:
            return {"articles": 0, "opportunity_signals": 0, "insights": 0}

        placeholders = ",".join("?" * len(old_ids))

        # Delete related opportunity signals
        try:
            cursor.execute(
                f"DELETE FROM opportunity_signals WHERE article_id IN ({placeholders})",
                old_ids,
            )
            deleted_signals = cursor.rowcount
        except Exception:
            deleted_signals = 0

        # Delete related insights
        try:
            cursor.execute(
                f"DELETE FROM insights WHERE article_id IN ({placeholders})",
                old_ids,
            )
            deleted_insights = cursor.rowcount
        except Exception:
            deleted_insights = 0

        # Delete the old articles
        cursor.execute(
            f"DELETE FROM articles WHERE id IN ({placeholders})",
            old_ids,
        )
        deleted_articles = cursor.rowcount

        logger.info(
            f"Cleanup: removed {deleted_articles} articles, "
            f"{deleted_signals} opportunity signals, "
            f"{deleted_insights} insights older than {max_age_days} days"
        )

        return {
            "articles": deleted_articles,
            "opportunity_signals": deleted_signals,
            "insights": deleted_insights,
        }
