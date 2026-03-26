"""Report generation for AI Deployment Research Monitor."""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from ..config import REPORTS_DIR, TARGET_COMPANIES, ANTHROPIC_API_KEY, CLAUDE_MODEL, TREND_CATEGORIES
from ..database import (
    get_articles_for_report,
    get_recent_insights,
    insert_report,
    get_database_stats,
    get_recent_opportunity_signals,
    get_opportunity_stats,
    get_company_intel,
)

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate research reports from collected data."""

    def __init__(self, output_dir: Path = None):
        self.output_dir = output_dir or REPORTS_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize Claude client for executive summaries
        self.client = None
        if ANTHROPIC_AVAILABLE and ANTHROPIC_API_KEY:
            try:
                self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            except Exception as e:
                logger.warning(f"Failed to initialize Anthropic client: {e}")

    def _format_date(self, dt: datetime) -> str:
        """Format datetime for display."""
        return dt.strftime("%B %d, %Y")

    def _call_claude(self, prompt: str, system: str = None, max_tokens: int = 2000) -> Optional[str]:
        """Make a Claude API call with error handling."""
        if not self.client:
            return None
        try:
            kwargs = {
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            response = self.client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return None

    def _categorize_articles(self, articles: List[Dict]) -> Dict[str, List[Dict]]:
        """Categorize articles by their category field."""
        categories = {
            "deployment_model": [], "metrics": [], "case_study": [],
            "trend": [], "opinion": [], "news": [], "technical": [],
            "funding": [], "product_launch": [], "other": [],
        }
        for article in articles:
            category = article.get("category", "other")
            if category in categories:
                categories[category].append(article)
            else:
                categories["other"].append(article)
        return categories

    def _get_top_sources(self, articles: List[Dict], n: int = 5) -> List[tuple]:
        """Get the top n sources by article count."""
        source_counts = Counter(a.get("source_name", "Unknown") for a in articles)
        return source_counts.most_common(n)

    def _get_mentioned_companies(self, articles: List[Dict]) -> List[tuple]:
        """Get companies mentioned across all articles."""
        company_counts = Counter()
        for article in articles:
            keywords = article.get("keywords_matched") or "[]"
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except json.JSONDecodeError:
                    keywords = []
            if not keywords:
                keywords = []
            for company in TARGET_COMPANIES:
                if company.lower() in [k.lower() for k in keywords if k]:
                    company_counts[company] += 1
            title = (article.get("title") or "").lower()
            summary = (article.get("content_summary") or "").lower()
            content = (article.get("full_content") or "").lower()
            text = f"{title} {summary} {content}"
            for company in TARGET_COMPANIES:
                if company.lower() in text:
                    company_counts[company] += 1
        return company_counts.most_common(15)

    def _analyze_trend_coverage(self, articles: List[Dict]) -> Dict[str, int]:
        """Analyze which trend categories are most covered."""
        trend_counts = {}
        for category, keywords in TREND_CATEGORIES.items():
            count = 0
            for article in articles:
                title = (article.get("title") or "").lower()
                content = (article.get("content") or "").lower()
                summary = (article.get("content_summary") or "").lower()
                text = f"{title} {content} {summary}"
                if any(kw.lower() in text for kw in keywords):
                    count += 1
            if count > 0:
                trend_counts[category] = count
        return trend_counts

    def _get_reddit_insights(self, articles: List[Dict]) -> List[Dict]:
        """Extract Reddit-specific articles with community metrics."""
        reddit_articles = []
        for article in articles:
            source = article.get("source_name", "")
            url = article.get("url", "")
            is_reddit = "r/" in source or "reddit" in source.lower() or "reddit.com" in url
            if is_reddit:
                content = article.get("full_content", "") or ""
                metadata = {}
                if "[METADATA]" in content:
                    try:
                        meta_str = content.split("[METADATA]")[1]
                        metadata = json.loads(meta_str)
                        content = content.split("[METADATA]")[0].strip()
                    except:
                        pass
                subreddit = metadata.get("subreddit", "")
                if not subreddit and "reddit.com/r/" in url:
                    try:
                        subreddit = url.split("/r/")[1].split("/")[0]
                    except:
                        subreddit = "unknown"
                if not subreddit:
                    subreddit = source.replace("r/", "") if "r/" in source else "unknown"
                reddit_articles.append({
                    "title": article.get("title", ""),
                    "url": url,
                    "subreddit": subreddit,
                    "score": metadata.get("score", 0),
                    "num_comments": metadata.get("num_comments", 0),
                    "content": content[:500] if content else "",
                    "relevance_score": article.get("relevance_score", 0),
                    "summary": article.get("content_summary", ""),
                })
        return sorted(reddit_articles, key=lambda x: (x["score"] + x["num_comments"], x["relevance_score"]), reverse=True)

    # =========================================================================
    # AI-POWERED DEEP ANALYSIS (Multi-pass)
    # =========================================================================

    def _generate_deep_article_analyses(self, articles: List[Dict]) -> Optional[str]:
        """
        Pass 1: Generate deep dives on the top 5 most important articles.
        Each gets its own mini-analysis with strategic implications.
        """
        if not self.client:
            return None

        # Prepare the top articles with full context
        article_blocks = []
        for i, article in enumerate(articles[:8]):
            title = article.get("title", "")
            source = article.get("source_name", "")
            summary = article.get("content_summary", "")
            content = article.get("full_content", "") or ""
            url = article.get("url", "")
            category = article.get("category", "")
            score = article.get("relevance_score", 0)

            # Clean content
            if "[METADATA]" in content:
                content = content.split("[METADATA]")[0].strip()

            block = f"ARTICLE {i+1}: [{source}] {title}\n"
            block += f"Category: {category} | Relevance: {score:.2f}\n"
            block += f"URL: {url}\n"
            if summary:
                block += f"AI Summary: {summary}\n"
            if content:
                block += f"Content excerpt: {content[:1500]}\n"
            article_blocks.append(block)

        system = """You are a senior AI industry strategist writing deep-dive analyses for a research
briefing. Your audience is a GSB student studying AI deployment patterns to identify future business
opportunities. Write with the analytical depth of a McKinsey partner but the clarity of a great
business journalist. Every analysis should answer: "So what? What should I do with this information?" """

        prompt = f"""Analyze these top articles and write a DEEP DIVE section for an executive briefing.

{chr(10).join(article_blocks)}

For the 5 most strategically important articles, write a deep dive that includes:

For each article:
### [Compelling headline that captures the strategic insight, not just the topic]

**The Signal:** What happened and why it matters (2-3 sentences, specific facts)

**Strategic Context:** How this fits into the broader AI deployment landscape. Connect it to:
- Market dynamics (who wins, who loses)
- Technology maturity curves
- Adoption patterns across verticals

**The Opportunity:** Concrete, actionable takeaway — what should someone building or investing
in AI do based on this? Be specific about timing, positioning, and competitive dynamics.

**Conviction Level:** High/Medium/Low — how confident should the reader be in acting on this signal?

Write approximately 800-1000 words total across all 5 deep dives.
Be bold in your analysis — don't hedge everything. Take clear positions on what matters."""

        return self._call_claude(prompt, system, 3000)

    def _generate_opportunity_synthesis(self, articles: List[Dict]) -> Optional[str]:
        """
        Pass 2: Synthesize opportunity signals into a strategic narrative.
        """
        if not self.client:
            return None

        # Gather all intelligence
        try:
            opp_signals = get_recent_opportunity_signals(limit=20)
        except Exception:
            opp_signals = []

        try:
            companies = get_company_intel(limit=15)
        except Exception:
            companies = []

        try:
            opp_stats = get_opportunity_stats()
        except Exception:
            opp_stats = {}

        # Prepare signal data
        signal_data = []
        for sig in opp_signals:
            signal_data.append(
                f"- [{sig.get('signal_type', '')}] {sig.get('company_name', 'Unknown')}: "
                f"{sig.get('opportunity_summary', '')} "
                f"(strength: {sig.get('signal_strength', 0):.0%}, "
                f"vertical: {sig.get('industry_vertical', 'unknown')}, "
                f"stage: {sig.get('deployment_stage', 'unknown')})"
            )

        company_data = []
        for c in companies[:10]:
            company_data.append(
                f"- {c.get('company_name', '')}: {c.get('total_mentions', 0)} mentions, "
                f"signal: {c.get('latest_signal_type', '-')}, "
                f"stage: {c.get('latest_deployment_stage', '-')}, "
                f"industry: {c.get('industry', '-')}"
            )

        # Prepare article summaries for context
        article_context = []
        for a in articles[:20]:
            summary = a.get("content_summary", "")
            if summary:
                article_context.append(f"- [{a.get('source_name', '')}] {a.get('title', '')}: {summary}")

        system = """You are a venture strategist writing for someone identifying the most promising
AI deployment opportunities. Synthesize raw signals into clear, prioritized investment/build
theses. Be opinionated and specific. Avoid generic "AI is transforming X" statements — instead
say "Company Y's move into Z vertical signals a $B opportunity because..."."""

        prompt = f"""Synthesize the following intelligence into a strategic opportunity analysis.

OPPORTUNITY SIGNALS ({len(opp_signals)} detected):
{chr(10).join(signal_data) if signal_data else "No signals detected yet."}

COMPANY INTELLIGENCE ({len(companies)} tracked):
{chr(10).join(company_data) if company_data else "No company data yet."}

ARTICLE CONTEXT:
{chr(10).join(article_context) if article_context else "No article summaries available."}

Write a synthesis (approximately 600-800 words) structured as:

## Opportunity Radar

### Tier 1: Act Now (highest conviction opportunities)
- 1-2 opportunities where the signal is strong and the window is closing
- Be specific about what to build, invest in, or position for

### Tier 2: Build Position (emerging opportunities)
- 2-3 opportunities that are forming but need more validation
- What would confirm or disconfirm these opportunities?

### Tier 3: Monitor (early signals)
- 2-3 weak signals that could become significant
- What leading indicators to watch

## Competitive Power Map
Brief analysis of who is winning and losing across the AI stack:
- Model layer (OpenAI vs Anthropic vs open source)
- Infrastructure layer (cloud vs specialized)
- Application layer (vertical AI vs horizontal)
- Developer tools layer

## Contrarian Takes
1-2 views that go against the prevailing narrative. What is the market
getting wrong about AI deployment?

Be bold, specific, and actionable throughout."""

        return self._call_claude(prompt, system, 3000)

    def _generate_weekly_synthesis(self, articles: List[Dict], insights: List[Dict]) -> Optional[str]:
        """
        Pass 3: Generate the final weekly synthesis — the "so what" of everything.
        """
        if not self.client:
            return None

        # Prepare condensed data
        article_summaries = []
        for a in articles[:25]:
            summary = a.get("content_summary", "")
            source = a.get("source_name", "")
            title = a.get("title", "")
            if title:
                line = f"- [{source}] {title}"
                if summary:
                    line += f": {summary}"
                article_summaries.append(line)

        insight_texts = [f"- {i.get('insight_text', '')}" for i in insights[:15] if i.get("insight_text")]

        trend_counts = self._analyze_trend_coverage(articles)
        trend_text = ", ".join([f"{k}: {v} articles" for k, v in
                                sorted(trend_counts.items(), key=lambda x: x[1], reverse=True)])

        mentioned_companies = self._get_mentioned_companies(articles)
        company_text = ", ".join([f"{c.title()} ({n})" for c, n in mentioned_companies[:10]])

        system = """You are a senior AI strategist writing the opening executive summary for a weekly
intelligence briefing. This summary is the MOST READ section — it must be dense with insight,
zero fluff. Your reader is a Stanford GSB student studying AI deployment strategies who will use
this to make career and investment decisions. Write with the precision of a Bloomberg analyst
and the strategic depth of a Sequoia partner."""

        prompt = f"""Write the Executive Summary section (400-500 words) for this week's AI Deployment
Intelligence Briefing.

COVERAGE THIS WEEK:
- {len(articles)} articles analyzed across {len(set(a.get('source_name', '') for a in articles))} sources
- Trend distribution: {trend_text}
- Top companies: {company_text}

ARTICLE SUMMARIES:
{chr(10).join(article_summaries)}

KEY INSIGHTS:
{chr(10).join(insight_texts) if insight_texts else "No structured insights extracted."}

Write a dense, insight-packed executive summary that:

1. Opens with the single most important thing that happened this week in AI deployment
   (1-2 sentences, punchy)

2. Identifies 3 macro-themes emerging from the coverage:
   - For each theme: what's happening, why now, and what it means
   - Connect themes to each other where possible

3. Names specific winners and losers this week (companies, approaches, or verticals)

4. Closes with a "bottom line" — the one thing the reader should take away and act on

Rules:
- No generic statements like "AI continues to evolve"
- Every sentence must contain a specific fact, company name, or data point
- Use strong verbs: "signals", "accelerates", "threatens", "validates"
- Write in present tense for urgency"""

        return self._call_claude(prompt, system, 1500)

    # =========================================================================
    # REPORT GENERATION
    # =========================================================================

    def generate_executive_briefing(
        self,
        weeks_back: int = 1,
        min_relevance: float = 0.5,
    ) -> str:
        """
        Generate a comprehensive, multi-pass executive briefing.
        Uses up to 3 Claude calls for deep, synthesized analysis.
        """
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(weeks=weeks_back)

        articles = get_articles_for_report(
            since=start_date, min_relevance=min_relevance, limit=100,
        )

        if not articles:
            return "# Executive Briefing\n\nNo relevant articles found for this period.\n"

        insights = get_recent_insights(limit=30)
        stats = get_database_stats()
        categorized = self._categorize_articles(articles)
        mentioned_companies = self._get_mentioned_companies(articles)
        trend_counts = self._analyze_trend_coverage(articles)
        top_articles = sorted(articles, key=lambda x: x.get("relevance_score", 0), reverse=True)

        total_relevant = len([a for a in articles if a.get("relevance_score", 0) >= 0.6])

        report = []

        # ── Header ──
        report.append("# AI Deployment Intelligence Briefing")
        report.append(f"**Period:** {self._format_date(start_date)} — {self._format_date(end_date)}")
        report.append(f"**Generated:** {self._format_date(datetime.utcnow())}")
        report.append(
            f"**Coverage:** {len(articles)} articles from "
            f"{len(set(a.get('source_name', '') for a in articles))} sources | "
            f"**High relevance:** {total_relevant} | "
            f"**Opportunity signals:** {stats.get('total_opportunity_signals', 0)} | "
            f"**Companies tracked:** {stats.get('total_companies_tracked', 0)}"
        )
        report.append("\n---\n")

        # ── Pass 1: Executive Summary (AI-generated synthesis) ──
        if self.client:
            exec_summary = self._generate_weekly_synthesis(articles, insights)
            if exec_summary:
                report.append("## Executive Summary\n")
                report.append(exec_summary)
                report.append("\n---\n")

        # ── Pass 2: Deep Dives on Top Articles ──
        if self.client and top_articles:
            deep_dives = self._generate_deep_article_analyses(top_articles)
            if deep_dives:
                report.append("## Deep Dives: This Week's Most Important Signals\n")
                report.append(deep_dives)
                report.append("\n---\n")

        # ── Pass 3: Opportunity Synthesis ──
        if self.client:
            opp_synthesis = self._generate_opportunity_synthesis(articles)
            if opp_synthesis:
                report.append(opp_synthesis)
                report.append("\n---\n")

        # ── Fallback: Non-AI sections (always included for data richness) ──

        # Opportunity Intelligence (structured data)
        try:
            opp_signals = get_recent_opportunity_signals(limit=15)
            if opp_signals:
                report.append("## Opportunity Signal Log\n")
                report.append("*Raw signals detected by AI analysis:*\n")

                by_type = {}
                for sig in opp_signals:
                    sig_type = sig.get("signal_type", "other").replace("_", " ").title()
                    if sig_type not in by_type:
                        by_type[sig_type] = []
                    by_type[sig_type].append(sig)

                for sig_type, sigs in by_type.items():
                    report.append(f"\n### {sig_type}\n")
                    for sig in sigs[:5]:
                        company = sig.get("company_name", "")
                        summary = sig.get("opportunity_summary", "")
                        title = sig.get("article_title", "")
                        url = sig.get("article_url", "")
                        strength = sig.get("signal_strength", 0)
                        vertical = sig.get("industry_vertical", "")
                        stage = sig.get("deployment_stage", "")

                        line = f"- **[{title}]({url})**"
                        if company:
                            line += f" — *{company}*"
                        line += f" ({int(strength*100)}% conviction)"
                        report.append(line)
                        if summary:
                            report.append(f"  {summary}")
                        meta = []
                        if vertical:
                            meta.append(f"Vertical: {vertical}")
                        if stage:
                            meta.append(f"Stage: {stage}")
                        if meta:
                            report.append(f"  *{' | '.join(meta)}*")
                        report.append("")

                report.append("---\n")
        except Exception as e:
            logger.debug(f"Could not generate opportunity section: {e}")

        # Company Intelligence Table
        try:
            company_intel = get_company_intel(limit=12)
            if company_intel:
                report.append("## Company Intelligence Tracker\n")
                report.append("| Company | Mentions | Latest Signal | Stage | Industry |")
                report.append("|---------|----------|---------------|-------|----------|")
                for c in company_intel:
                    name = c.get("company_name", "")
                    mentions = c.get("total_mentions", 0)
                    sig = (c.get("latest_signal_type") or "-").replace("_", " ").title()
                    stage = (c.get("latest_deployment_stage") or "-").title()
                    industry = c.get("industry") or "-"
                    report.append(f"| {name} | {mentions} | {sig} | {stage} | {industry} |")
                report.append("\n---\n")
        except Exception:
            pass

        # Trend Heatmap
        if trend_counts:
            report.append("## Trend Heatmap\n")
            sorted_trends = sorted(trend_counts.items(), key=lambda x: x[1], reverse=True)
            total_trend_articles = sum(c for _, c in sorted_trends)

            for trend, count in sorted_trends:
                pct = int(count / total_trend_articles * 100) if total_trend_articles > 0 else 0
                bar_len = min(int(pct / 5), 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                report.append(f"**{trend}** [{bar}] {count} ({pct}%)")
            report.append("\n---\n")

        # Community Intelligence (Reddit)
        reddit_insights = self._get_reddit_insights(articles)
        if reddit_insights:
            report.append("## Practitioner Pulse\n")
            report.append("*What the AI community is actually talking about:*\n")

            subreddit_posts = {}
            for post in reddit_insights[:15]:
                sub = post.get("subreddit", "unknown")
                if sub not in subreddit_posts:
                    subreddit_posts[sub] = []
                subreddit_posts[sub].append(post)

            for subreddit, posts in list(subreddit_posts.items())[:5]:
                total_engagement = sum(p["score"] + p["num_comments"] for p in posts)
                report.append(f"\n### r/{subreddit} ({len(posts)} posts, {total_engagement:,} engagement)\n")
                for post in posts[:3]:
                    report.append(f"**[{post['title']}]({post['url']})**")
                    report.append(f"*{post['score']:,} upvotes | {post['num_comments']:,} comments*")
                    if post.get("summary"):
                        report.append(f"> {post['summary']}")
                    elif post.get("content") and len(post["content"]) > 50:
                        excerpt = post["content"][:200].strip()
                        last_break = max(excerpt.rfind('.'), excerpt.rfind('\n'))
                        if last_break > 80:
                            excerpt = excerpt[:last_break + 1]
                        else:
                            excerpt += "..."
                        report.append(f"> {excerpt}")
                    report.append("")

            report.append("---\n")

        # Must-Read List (quick reference)
        report.append("## Must-Read List\n")
        for i, article in enumerate(top_articles[:10], 1):
            title = article.get("title", "Untitled")
            url = article.get("url", "")
            source = article.get("source_name", "")
            score = article.get("relevance_score", 0)
            summary = article.get("content_summary", "")
            report.append(f"**{i}. [{title}]({url})**")
            report.append(f"*{source} | Relevance: {score:.2f}*")
            if summary:
                report.append(f"{summary}")
            report.append("")

        # Footer
        report.append("\n---")
        report.append(
            f"*Generated by AI Deployment Research Monitor | "
            f"{stats.get('active_sources', 0)} active sources | "
            f"{stats.get('total_articles', 0)} total articles indexed*"
        )

        return "\n".join(report)

    def generate_weekly_report(
        self,
        weeks_back: int = 1,
        min_relevance: float = 0.5,
    ) -> str:
        """Generate a comprehensive weekly report (uses the executive briefing engine)."""
        # The executive briefing IS the weekly report now — same multi-pass approach
        return self.generate_executive_briefing(weeks_back=weeks_back, min_relevance=min_relevance)

    def generate_daily_digest(self, min_relevance: float = 0.5) -> str:
        """Generate a focused daily digest with AI analysis."""
        start_date = datetime.utcnow() - timedelta(days=1)

        articles = get_articles_for_report(
            since=start_date, min_relevance=min_relevance, limit=30,
        )

        if not articles:
            return "# Daily Digest\n\nNo new relevant articles in the past 24 hours.\n"

        top_articles = sorted(articles, key=lambda x: x.get("relevance_score", 0), reverse=True)

        report = []
        report.append(f"# AI Deployment Monitor — Daily Digest")
        report.append(f"**{self._format_date(datetime.utcnow())}** | {len(articles)} new articles\n")
        report.append("---\n")

        # AI-generated daily summary
        if self.client and len(articles) >= 3:
            article_data = []
            for a in articles[:15]:
                summary = a.get("content_summary", "")
                if a.get("title"):
                    line = f"- [{a.get('source_name', '')}] {a['title']}"
                    if summary:
                        line += f": {summary}"
                    article_data.append(line)

            daily_prompt = f"""Write a quick daily briefing (200-300 words) on today's AI deployment news.

TODAY'S ARTICLES:
{chr(10).join(article_data)}

Structure:
1. **Today's Lead** (1-2 sentences): The single most important development
2. **Also Notable** (3-4 bullet points): Other significant items, each with a "so what"
3. **Quick Take**: One sentence on what today's coverage tells us about the market direction

Be specific, name companies, skip the filler."""

            daily_summary = self._call_claude(
                daily_prompt,
                system="You write punchy daily intelligence briefs for AI industry strategists.",
                max_tokens=800,
            )
            if daily_summary:
                report.append(daily_summary)
                report.append("\n---\n")

        # Today's top articles
        report.append(f"## Today's Top {min(len(top_articles), 10)} Articles\n")
        for i, article in enumerate(top_articles[:10], 1):
            title = article.get("title", "Untitled")
            url = article.get("url", "")
            source = article.get("source_name", "")
            score = article.get("relevance_score", 0)
            summary = article.get("content_summary", "")
            report.append(f"**{i}. [{title}]({url})**")
            report.append(f"*{source} | {score:.2f}*")
            if summary:
                report.append(f"{summary}")
            report.append("")

        # Today's opportunity signals
        try:
            signals = get_recent_opportunity_signals(limit=5)
            today_signals = [s for s in signals if s.get("created_at", "")[:10] == datetime.utcnow().strftime("%Y-%m-%d")]
            if today_signals:
                report.append("## Today's Opportunity Signals\n")
                for sig in today_signals:
                    sig_type = sig.get("signal_type", "").replace("_", " ").title()
                    company = sig.get("company_name", "")
                    summary = sig.get("opportunity_summary", "")
                    strength = sig.get("signal_strength", 0)
                    report.append(f"- **{sig_type}** {f'({company})' if company else ''} — {summary} *({int(strength*100)}% conviction)*")
                report.append("")
        except Exception:
            pass

        report.append("\n---")
        report.append("*AI Deployment Research Monitor — Daily Digest*")

        return "\n".join(report)

    def save_report(
        self,
        content: str,
        report_type: str = "weekly",
        filename: str = None,
    ) -> Path:
        """Save report to file and database."""
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        if not filename:
            filename = f"{report_type}_report_{date_str}.md"

        filepath = self.output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Report saved to {filepath}")

        try:
            insert_report(
                report_type=report_type,
                title=f"{report_type.title()} Report - {date_str}",
                content=content,
                articles_included=[],
                stats=get_database_stats(),
            )
        except Exception as e:
            logger.error(f"Failed to save report to database: {e}")

        return filepath

    def generate_and_save_weekly(self) -> Path:
        """Generate and save a weekly report."""
        content = self.generate_weekly_report()
        return self.save_report(content, "weekly")

    def generate_and_save_daily(self) -> Path:
        """Generate and save a daily digest."""
        content = self.generate_daily_digest()
        return self.save_report(content, "daily")
