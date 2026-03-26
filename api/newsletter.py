"""Vercel serverless function — triggered by Vercel Cron to run the newsletter pipeline."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Add project root to path so imports work in Vercel
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    """Handle GET requests from Vercel Cron or manual triggers."""

    def do_GET(self):
        # Simple auth: require CRON_SECRET header to prevent public abuse
        expected_secret = os.getenv("CRON_SECRET", "")
        provided_secret = self.headers.get("Authorization", "").replace("Bearer ", "")

        if expected_secret and provided_secret != expected_secret:
            self._respond(401, {"error": "Unauthorized"})
            return

        query = parse_qs(urlparse(self.path).query)
        step = query.get("step", ["scrape"])[0]
        report_type = query.get("type", ["daily"])[0]

        try:
            if step == "scrape":
                result = self._step_scrape()
            elif step == "analyze":
                result = self._step_analyze()
            elif step == "report":
                result = self._step_report(report_type)
            elif step == "status":
                result = self._step_status()
            else:
                result = {"error": f"Unknown step: {step}"}

            self._respond(200, {"ok": True, "step": step, **result})

        except Exception as e:
            import traceback
            self._respond(500, {
                "error": str(e),
                "traceback": traceback.format_exc(),
            })

    def do_POST(self):
        """Also accept POST for manual triggers."""
        self.do_GET()

    def _respond(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _step_status(self):
        """Health check — verify imports, DB, and API key."""
        from src.config import ANTHROPIC_API_KEY, DATABASE_PATH
        from src.database import init_database
        init_database()
        return {
            "status": "ok",
            "api_key_set": bool(ANTHROPIC_API_KEY),
            "db_path": str(DATABASE_PATH),
        }

    def _step_scrape(self):
        """Scrape HackerNews only (fastest, no auth needed)."""
        from src.database import (
            init_database, seed_sources, insert_article,
            update_source_last_checked, get_source_by_type,
        )
        from src.scrapers import HackerNewsScraper
        from src.processors import RelevanceScorer

        init_database()
        seed_sources()

        scorer = RelevanceScorer()
        scraped = 0

        try:
            scraper = HackerNewsScraper()
            items = scraper.run()
            db_sources = get_source_by_type("hackernews")
            source_id_map = {s["name"]: s["id"] for s in db_sources}

            for item in items[:30]:  # Limit to 30 items to stay within timeout
                relevance = scorer.score(
                    item.get("title", ""), item.get("content", "")
                )
                if relevance["score"] < 0.3:
                    continue
                source_name = item.get("source_name", "hackernews")
                source_id = source_id_map.get(
                    source_name,
                    list(source_id_map.values())[0] if source_id_map else 1,
                )
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
                    scraped += 1

            for db_source in db_sources:
                update_source_last_checked(db_source["id"])
        except Exception as e:
            return {"scraped": scraped, "error": str(e)}

        return {"scraped": scraped}

    def _step_analyze(self):
        """Analyze unprocessed articles with Claude (limit 5 per call)."""
        from src.database import (
            init_database, get_unprocessed_articles, update_article_analysis,
        )
        from src.processors import RelevanceScorer, ContentAnalyzer
        from src.config import RELEVANCE_THRESHOLD

        init_database()
        scorer = RelevanceScorer()
        analyzer = ContentAnalyzer()
        analyzed = 0

        articles = get_unprocessed_articles(limit=5)
        for article in articles:
            relevance = scorer.score(
                article["title"], article.get("full_content", "")
            )
            summary, sentiment, category = "", "neutral", "news"
            if relevance["score"] >= RELEVANCE_THRESHOLD:
                analysis = analyzer.analyze(
                    article["title"],
                    article.get("full_content", ""),
                    article.get("url", ""),
                )
                if analysis:
                    summary = analysis.get("summary", "")
                    sentiment = analysis.get("sentiment", "neutral")
                    category = analysis.get("category", "news")
            update_article_analysis(
                article_id=article["id"],
                summary=summary,
                sentiment=sentiment,
                category=category,
                relevance_score=relevance["score"],
                keywords_matched=relevance["all_keywords_matched"],
            )
            analyzed += 1

        return {"analyzed": analyzed}

    def _step_report(self, report_type):
        """Generate and optionally email a report."""
        from src.database import init_database
        from src.reports import ReportGenerator
        from src.emailer import send_newsletter

        init_database()
        generator = ReportGenerator()

        if report_type == "executive":
            content = generator.generate_executive_briefing()
        elif report_type == "weekly":
            content = generator.generate_weekly_report()
        else:
            content = generator.generate_daily_digest()

        generator.save_report(content, report_type)
        email_sent = send_newsletter(content, report_type)

        return {"report_type": report_type, "email_sent": email_sent}
