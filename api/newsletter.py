"""Vercel serverless function — triggered by Vercel Cron to run the newsletter pipeline."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler

# Add project root to path so imports work in Vercel
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class handler(BaseHTTPRequestHandler):
    """Handle GET requests from Vercel Cron or manual triggers."""

    def do_GET(self):
        # Simple auth: require CRON_SECRET header to prevent public abuse
        expected_secret = os.getenv("CRON_SECRET", "")
        provided_secret = self.headers.get("Authorization", "").replace("Bearer ", "")

        if expected_secret and provided_secret != expected_secret:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
            return

        try:
            from src.database import init_database, seed_sources
            from src.scrapers import RedditScraper, HackerNewsScraper, RSSFeedScraper
            from src.processors import RelevanceScorer, ContentAnalyzer
            from src.reports import ReportGenerator
            from src.emailer import send_newsletter
            from src.database import (
                insert_article,
                update_article_analysis,
                update_source_last_checked,
                get_source_by_type,
                get_unprocessed_articles,
            )
            from src.config import RELEVANCE_THRESHOLD

            # Initialize
            init_database()
            seed_sources()

            # Determine report type from query param (default: daily)
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            report_type = query.get("type", ["daily"])[0]

            results = {"scraped": 0, "analyzed": 0, "email_sent": False}

            # --- Scrape ---
            scorer = RelevanceScorer()
            for source_type, scraper in [
                ("reddit", RedditScraper()),
                ("hackernews", HackerNewsScraper()),
                ("rss", RSSFeedScraper()),
            ]:
                try:
                    items = scraper.run()
                    db_sources = get_source_by_type(source_type)
                    source_id_map = {s["name"]: s["id"] for s in db_sources}

                    for item in items:
                        relevance = scorer.score(item.get("title", ""), item.get("content", ""))
                        if relevance["score"] < 0.3:
                            continue
                        source_name = item.get("source_name", source_type)
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
                            results["scraped"] += 1

                    for db_source in db_sources:
                        update_source_last_checked(db_source["id"])
                except Exception as e:
                    print(f"Scrape error ({source_type}): {e}")

            # --- Analyze ---
            try:
                analyzer = ContentAnalyzer()
                articles = get_unprocessed_articles(limit=30)
                for article in articles:
                    relevance = scorer.score(article["title"], article.get("full_content", ""))
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
                    results["analyzed"] += 1
            except Exception as e:
                print(f"Analysis error: {e}")

            # --- Generate & Send ---
            try:
                generator = ReportGenerator()
                if report_type == "executive":
                    content = generator.generate_executive_briefing()
                elif report_type == "weekly":
                    content = generator.generate_weekly_report()
                else:
                    content = generator.generate_daily_digest()

                generator.save_report(content, report_type)
                results["email_sent"] = send_newsletter(content, report_type)
            except Exception as e:
                print(f"Report/email error: {e}")

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True,
                "results": results,
            }).encode())

        except Exception as e:
            import traceback
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": str(e),
                "traceback": traceback.format_exc(),
            }).encode())

    def do_POST(self):
        """Also accept POST for manual triggers."""
        self.do_GET()
