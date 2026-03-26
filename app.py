"""
AI Deployment Research Monitor - Web UI
Built with Streamlit

Run with: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import json
import time
from collections import Counter

# Import our modules
from src.config import (
    DATABASE_PATH,
    RELEVANCE_THRESHOLD,
    TARGET_COMPANIES,
    TREND_CATEGORIES,
    PRIMARY_KEYWORDS,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    REDDIT_CLIENT_ID,
    INDUSTRY_VERTICALS,
)
from src.database import (
    init_database,
    seed_sources,
    get_database_stats,
    get_recent_articles,
    get_articles_for_report,
    search_articles,
    get_unprocessed_articles,
    insert_article,
    update_article_analysis,
    update_source_last_checked,
    get_source_by_type,
    insert_opportunity_signal,
    get_recent_opportunity_signals,
    get_opportunity_stats,
    upsert_company_intel,
    get_company_intel,
    update_article_opportunity,
    cleanup_old_articles,
    get_latest_report,
    get_all_reports,
    insert_report,
)
from src.scrapers import RedditScraper, HackerNewsScraper, RSSFeedScraper
from src.processors import RelevanceScorer, ContentAnalyzer
from src.reports import ReportGenerator

# Page config
st.set_page_config(
    page_title="AI Deployment Monitor",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Modern light CSS
st.markdown("""
<style>
    /* Global overrides for light, airy feel */
    section[data-testid="stSidebar"] {
        background-color: #F8FAFC;
        border-right: 1px solid #E2E8F0;
    }
    section[data-testid="stSidebar"] .stRadio label {
        font-size: 0.95rem;
        padding: 0.35rem 0;
    }
    .main-header {
        font-size: 1.8rem;
        font-weight: 600;
        color: #1E293B;
        margin-bottom: 0;
        letter-spacing: -0.02em;
    }
    .sub-header {
        font-size: 0.95rem;
        color: #64748B;
        margin-top: 0;
    }
    .trend-tag {
        background: #EEF2FF;
        color: #4338CA;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        margin-right: 0.4rem;
        display: inline-block;
        font-weight: 500;
    }
    .company-tag {
        background: #FFF7ED;
        color: #C2410C;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        font-size: 0.75rem;
        margin-right: 0.4rem;
        display: inline-block;
        font-weight: 500;
    }
    .score-high { color: #059669; font-weight: 600; }
    .score-medium { color: #D97706; font-weight: 600; }
    .score-low { color: #9CA3AF; }

    /* Clean metric cards */
    [data-testid="stMetric"] {
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 0.8rem 1rem;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        color: #64748B !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        font-weight: 600 !important;
        color: #1E293B !important;
    }

    /* Clean dividers */
    hr {
        border: none;
        border-top: 1px solid #E2E8F0;
        margin: 1.5rem 0;
    }

    /* Button styling */
    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
        font-size: 0.85rem;
        border: 1px solid #E2E8F0;
        transition: all 0.15s ease;
    }
    .stButton > button:hover {
        border-color: #4F46E5;
        color: #4F46E5;
    }
    .stButton > button[kind="primary"] {
        background-color: #4F46E5;
        border-color: #4F46E5;
    }

    /* Expander styling */
    .streamlit-expanderHeader {
        font-size: 0.9rem;
        font-weight: 500;
        color: #334155;
    }
</style>
""", unsafe_allow_html=True)


def init_session_state():
    """Initialize session state variables."""
    if "db_initialized" not in st.session_state:
        init_database()
        seed_sources()
        st.session_state.db_initialized = True

    # Run daily cleanup of articles older than 2 months
    today = datetime.now().strftime("%Y-%m-%d")
    if st.session_state.get("last_cleanup_date") != today:
        try:
            result = cleanup_old_articles(max_age_days=60)
            if result["articles"] > 0:
                st.toast(f"Cleaned up {result['articles']} articles older than 2 months")
        except Exception:
            pass  # Non-critical
        st.session_state.last_cleanup_date = today


def get_articles_df(limit=100, sort_by="relevance"):
    """Get articles as a pandas DataFrame."""
    articles = get_recent_articles(limit=limit, sort_by=sort_by)
    if not articles:
        return pd.DataFrame()

    df = pd.DataFrame(articles)

    # Parse dates
    if "discovered_date" in df.columns:
        df["discovered_date"] = pd.to_datetime(df["discovered_date"], errors="coerce")
    if "published_date" in df.columns:
        df["published_date"] = pd.to_datetime(df["published_date"], errors="coerce")

    return df


def render_sidebar():
    """Render the sidebar navigation."""
    st.sidebar.markdown(
        '<p style="font-size:1.1rem;font-weight:600;color:#1E293B;margin-bottom:0;">AI Monitor</p>'
        '<p style="font-size:0.75rem;color:#94A3B8;margin-top:0;">Deployment Research</p>',
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")

    # Navigation
    page = st.sidebar.radio(
        "Navigation",
        ["Dashboard", "Opportunities", "Articles", "Search", "Trends", "Reddit Pulse", "Reports", "Settings"],
        label_visibility="collapsed",
    )

    st.sidebar.markdown("---")

    # Quick stats in compact row
    stats = get_database_stats()
    c1, c2, c3 = st.sidebar.columns(3)
    c1.metric("Articles", stats.get("total_articles", 0))
    c2.metric("Relevant", stats.get("relevant_articles", 0))
    c3.metric("Sources", stats.get("active_sources", 0))

    st.sidebar.markdown("---")

    # Quick actions
    st.sidebar.caption("ACTIONS")

    if st.sidebar.button("Fetch New Content", icon=":material/refresh:", width="stretch"):
        with st.spinner("Fetching content..."):
            run_scraper()
        st.rerun()

    if st.sidebar.button("Analyze Articles", icon=":material/psychology:", width="stretch"):
        with st.spinner("Analyzing..."):
            run_analysis()
        st.rerun()

    return page


def render_dashboard():
    """Render the main dashboard."""
    st.markdown('<p class="main-header">AI Deployment Research Monitor</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Track the latest trends in AI deployment and enterprise adoption</p>', unsafe_allow_html=True)
    st.markdown("")

    # Top metrics row
    stats = get_database_stats()
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total Articles", stats.get("total_articles", 0))
    with col2:
        st.metric("Analyzed", stats.get("processed_articles", 0))
    with col3:
        st.metric("High Relevance", stats.get("relevant_articles", 0))
    with col4:
        st.metric("Insights", stats.get("total_insights", 0))

    st.markdown("")

    # Charts row
    col1, col2 = st.columns(2)

    df = get_articles_df(limit=200)

    with col1:
        st.markdown("##### Articles by Source")
        if not df.empty and "source_name" in df.columns:
            source_counts = df["source_name"].value_counts().head(10)
            fig = px.bar(
                x=source_counts.values,
                y=source_counts.index,
                orientation="h",
                labels={"x": "Count", "y": "Source"},
                color_discrete_sequence=["#6366F1"],
            )
            fig.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#475569"),
            )
            fig.update_xaxes(gridcolor="#F1F5F9")
            fig.update_yaxes(gridcolor="#F1F5F9")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No articles yet. Click 'Fetch New Content' to get started.")

    with col2:
        st.markdown("##### Top Companies Mentioned")
        if not df.empty:
            company_counts = count_companies(df)
            if company_counts:
                fig = px.pie(
                    values=list(company_counts.values())[:8],
                    names=list(company_counts.keys())[:8],
                    hole=0.45,
                    color_discrete_sequence=px.colors.qualitative.Pastel,
                )
                fig.update_layout(
                    height=300,
                    margin=dict(l=0, r=0, t=10, b=0),
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#475569"),
                )
                st.plotly_chart(fig, width="stretch")
            else:
                st.info("No company mentions found yet.")
        else:
            st.info("No data available.")

    st.markdown("---")

    # Recent high-relevance articles
    st.markdown("##### Top Relevant Articles")

    if not df.empty:
        top_articles = df.nlargest(5, "relevance_score") if "relevance_score" in df.columns else df.head(5)
        for _, article in top_articles.iterrows():
            render_article_card(article)
    else:
        st.info("No articles found. Start by fetching content from the sidebar.")


def render_articles_page():
    """Render the articles browser page."""
    st.markdown("## Article Browser")
    st.caption("Browse and filter all collected articles")
    st.markdown("")

    # Filters
    col1, col2, col3 = st.columns(3)

    with col1:
        sort_option = st.selectbox(
            "Sort by",
            ["Relevance Score", "Most Recent", "Source"],
        )

    with col2:
        min_score = st.slider(
            "Minimum Relevance",
            0.0, 1.0, 0.3, 0.1,
        )

    with col3:
        limit = st.selectbox(
            "Show",
            [25, 50, 100, 200],
            index=1,
        )

    sort_map = {
        "Relevance Score": "relevance",
        "Most Recent": "discovered",
        "Source": "discovered",
    }

    df = get_articles_df(limit=limit, sort_by=sort_map[sort_option])

    if not df.empty:
        # Filter by minimum score
        if "relevance_score" in df.columns:
            df = df[df["relevance_score"] >= min_score]

        st.markdown(f"**Showing {len(df)} articles**")

        # Display articles
        for _, article in df.iterrows():
            render_article_card(article, expanded=False)
    else:
        st.info("No articles found. Use the sidebar to fetch content.")


def render_search_page():
    """Render the search page."""
    st.markdown("## Search Articles")
    st.caption("Search through collected research articles")
    st.markdown("")

    # Search input
    query = st.text_input(
        "Search query",
        placeholder="e.g., AI agents, forward deployment, RAG...",
    )

    col1, col2 = st.columns([3, 1])
    with col2:
        search_limit = st.selectbox("Results", [20, 50, 100], index=0)

    if query:
        results = search_articles(query, limit=search_limit)

        if results:
            st.success(f"Found {len(results)} results for '{query}'")

            for article in results:
                render_article_card(article)
        else:
            st.warning(f"No results found for '{query}'")
    else:
        # Show suggested searches
        st.markdown("### Suggested Searches")

        suggestions = [
            "AI agents", "forward deployment", "enterprise AI",
            "RAG", "fine-tuning", "Anthropic", "OpenAI",
            "MLOps", "inference", "vector database"
        ]

        cols = st.columns(5)
        for i, suggestion in enumerate(suggestions):
            with cols[i % 5]:
                if st.button(suggestion, key=f"sug_{i}"):
                    st.session_state.search_query = suggestion
                    st.rerun()


def render_trends_page():
    """Render the trends analysis page."""
    st.markdown("## Trend Analysis")
    st.caption("Track emerging trends in AI deployment")
    st.markdown("")

    df = get_articles_df(limit=500)

    if df.empty:
        st.info("Not enough data for trend analysis. Fetch more content first.")
        return

    # Trend category analysis
    st.markdown("##### Trend Categories")

    trend_data = analyze_trends(df)

    if trend_data:
        # Create trend chart
        fig = px.bar(
            x=list(trend_data.keys()),
            y=list(trend_data.values()),
            labels={"x": "Category", "y": "Article Count"},
            color_discrete_sequence=["#6366F1"],
        )
        fig.update_layout(
            height=400,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#475569"),
        )
        fig.update_xaxes(gridcolor="#F1F5F9")
        fig.update_yaxes(gridcolor="#F1F5F9")
        st.plotly_chart(fig, width="stretch")

        # Show articles by category
        st.markdown("---")
        selected_category = st.selectbox(
            "Explore Category",
            list(TREND_CATEGORIES.keys()),
        )

        if selected_category:
            keywords = TREND_CATEGORIES[selected_category]
            st.markdown(f"**Keywords:** {', '.join(keywords)}")

            # Filter articles for this category
            category_articles = filter_by_keywords(df, keywords)
            if not category_articles.empty:
                st.markdown(f"**{len(category_articles)} articles in this category:**")
                for _, article in category_articles.head(10).iterrows():
                    render_article_card(article, expanded=False)
            else:
                st.info("No articles found in this category yet.")

    # Timeline chart
    st.markdown("---")
    st.markdown("##### Articles Over Time")

    if "discovered_date" in df.columns:
        df_timeline = df.copy()
        df_timeline["date"] = df_timeline["discovered_date"].dt.date
        daily_counts = df_timeline.groupby("date").size().reset_index(name="count")

        if not daily_counts.empty:
            fig = px.line(
                daily_counts,
                x="date",
                y="count",
                labels={"date": "Date", "count": "Articles"},
                color_discrete_sequence=["#6366F1"],
            )
            fig.update_layout(
                height=300,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#475569"),
            )
            fig.update_xaxes(gridcolor="#F1F5F9")
            fig.update_yaxes(gridcolor="#F1F5F9")
            st.plotly_chart(fig, width="stretch")


def _generate_reddit_ai_synthesis(reddit_df, top_posts_data, subreddit_stats):
    """Generate AI-powered synthesis of Reddit community discussions."""
    try:
        import anthropic
    except ImportError:
        return None

    if not ANTHROPIC_API_KEY:
        return None

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception:
        return None

    # Prepare discussion data for Claude
    post_blocks = []
    for i, post in enumerate(top_posts_data[:20]):
        title = post.get("title", "")
        subreddit = post.get("subreddit", "")
        score = post.get("score", 0)
        comments = post.get("num_comments", 0)
        content = post.get("content", "")[:600]
        summary = post.get("summary", "")

        block = f"[r/{subreddit}] {title} ({score} upvotes, {comments} comments)"
        if summary:
            block += f"\nSummary: {summary}"
        elif content:
            block += f"\nExcerpt: {content}"
        post_blocks.append(block)

    # Subreddit overview
    sub_overview = []
    for sub, count in subreddit_stats[:8]:
        sub_overview.append(f"r/{sub}: {count} posts")

    system = """You are a senior AI industry analyst writing the community intelligence section of a
weekly briefing for a Stanford GSB student studying AI deployment strategies. Your job is to decode
what real practitioners are saying on Reddit and translate it into strategic signals. Focus on what
the COMMUNITY'S behavior tells us about where AI deployment is headed — not just what the articles say."""

    prompt = f"""Analyze these {len(post_blocks)} Reddit discussions from AI practitioner communities and write
a strategic community intelligence briefing.

SUBREDDIT DISTRIBUTION:
{chr(10).join(sub_overview)}

TOP DISCUSSIONS:
{chr(10).join(post_blocks)}

Write a briefing (300-400 words) structured as:

## Community Intelligence

**The Vibe:** One punchy sentence capturing the overall mood of the AI practitioner community right now.

**Signal vs. Noise:** What are practitioners ACTUALLY focused on vs. what the media hype cycle says?
- Identify the gap between Twitter/media narrative and what builders are discussing on Reddit
- What topics get the most engagement (upvotes + comments) and what does that tell us?

**Practitioner Pain Points:** (2-3 bullets)
- What problems are people trying to solve? What tools/approaches are they struggling with?
- These pain points = potential startup/product opportunities

**Emerging Consensus:** (2-3 bullets)
- Where is the community converging on best practices or winning approaches?
- Are there any technology preferences forming (e.g., which frameworks, which models)?

**Contrarian Signals:**
- Any high-engagement posts that go against conventional wisdom? What are the "hot takes"?
- Minority opinions that might be leading indicators

**Deployment Readiness Check:**
Rate the community's overall production-readiness sentiment: Early Experimentation / Building Seriously / Production-Ready / Scaling & Optimizing

Rules:
- Name specific tools, frameworks, and companies mentioned
- Every bullet should contain a specific observation, not a generic statement
- Focus on deployment and implementation signals, not research breakthroughs
- Use present tense for urgency"""

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Reddit AI synthesis error: {e}")
        return None


def render_reddit_pulse_page():
    """Render the Reddit Pulse page - AI-powered community insights and sentiment."""
    st.markdown("## Reddit Pulse")
    st.caption("AI-powered analysis of practitioner communities on Reddit")
    st.markdown("")

    # Get all articles and filter for Reddit
    df = get_articles_df(limit=500)

    if df.empty:
        st.info("No articles found. Fetch content first to see Reddit discussions.")
        return

    # Filter for Reddit articles
    reddit_df = df[df["url"].str.contains("reddit.com", na=False)].copy()

    if reddit_df.empty:
        st.warning("No Reddit posts found. Try fetching new content.")
        return

    # Extract subreddit from URL
    def extract_subreddit(url):
        if pd.isna(url) or "reddit.com/r/" not in str(url):
            return "unknown"
        try:
            return url.split("/r/")[1].split("/")[0]
        except:
            return "unknown"

    reddit_df["subreddit"] = reddit_df["url"].apply(extract_subreddit)

    # Parse metadata from content for engagement metrics
    def parse_metadata(content):
        if pd.isna(content) or "[METADATA]" not in str(content):
            return {"score": 0, "num_comments": 0}
        try:
            meta_str = str(content).split("[METADATA]")[1]
            return json.loads(meta_str)
        except:
            return {"score": 0, "num_comments": 0}

    reddit_df["metadata"] = reddit_df["full_content"].apply(parse_metadata)
    reddit_df["score"] = reddit_df["metadata"].apply(lambda x: x.get("score", 0))
    reddit_df["num_comments"] = reddit_df["metadata"].apply(lambda x: x.get("num_comments", 0))
    reddit_df["engagement"] = reddit_df["score"] + reddit_df["num_comments"]

    # Parse dates for display
    if "discovered_date" in reddit_df.columns:
        reddit_df["display_date"] = pd.to_datetime(reddit_df["discovered_date"], errors="coerce")

    # Compute data used in multiple sections
    total_posts = len(reddit_df)
    total_engagement = reddit_df["engagement"].sum()
    top_posts = reddit_df.nlargest(20, "engagement")
    subreddit_counts = reddit_df["subreddit"].value_counts()

    # Top metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Reddit Posts", total_posts)
    with col2:
        st.metric("Subreddits", reddit_df["subreddit"].nunique())
    with col3:
        st.metric("Total Upvotes", f"{reddit_df['score'].sum():,}")
    with col4:
        st.metric("Total Comments", f"{reddit_df['num_comments'].sum():,}")

    st.markdown("---")

    # =========================================================================
    # AI-POWERED COMMUNITY INTELLIGENCE
    # =========================================================================

    # Prepare top posts data for AI analysis
    top_posts_data = []
    for _, row in top_posts.iterrows():
        content = row.get("full_content", "") or ""
        if "[METADATA]" in content:
            content = content.split("[METADATA]")[0].strip()
        top_posts_data.append({
            "title": row.get("title", ""),
            "subreddit": row.get("subreddit", ""),
            "score": row.get("score", 0),
            "num_comments": row.get("num_comments", 0),
            "content": content,
            "summary": row.get("content_summary", "") or "",
        })

    subreddit_stats = list(subreddit_counts.head(8).items())

    # =========================================================================
    # AI COMMUNITY SYNTHESIS (persisted to database)
    # =========================================================================

    # Load persisted synthesis from database
    saved_synthesis = get_latest_report("reddit_synthesis")
    saved_synthesis_content = saved_synthesis.get("content") if saved_synthesis else None
    saved_synthesis_date = saved_synthesis.get("generated_date", "")[:16] if saved_synthesis else None

    # Priority: session_state (just generated) > DB (persisted)
    synthesis = st.session_state.get("reddit_synthesis_content")
    synthesis_date = st.session_state.get("reddit_synthesis_date")

    if synthesis is None and saved_synthesis_content:
        synthesis = saved_synthesis_content
        synthesis_date = saved_synthesis_date
        st.session_state["reddit_synthesis_content"] = synthesis
        st.session_state["reddit_synthesis_date"] = synthesis_date

    if synthesis:
        # Show persisted synthesis
        if synthesis_date:
            st.caption(f"*Analysis generated: {synthesis_date}*")
        st.markdown(synthesis)
        # Re-analyze button
        if st.button("Re-analyze", type="secondary"):
            with st.spinner("Claude is re-analyzing community discussions..."):
                new_synthesis = _generate_reddit_ai_synthesis(reddit_df, top_posts_data, subreddit_stats)
                if new_synthesis:
                    insert_report(
                        report_type="reddit_synthesis",
                        title=f"Reddit Community Synthesis - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                        content=new_synthesis,
                        articles_included=[],
                        stats={"total_posts": total_posts, "total_engagement": int(total_engagement)},
                    )
                    st.session_state["reddit_synthesis_content"] = new_synthesis
                    st.session_state["reddit_synthesis_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    st.warning("AI analysis unavailable. Check API key.")
                st.rerun()
    else:
        # No synthesis exists — show generate button
        analyze_col1, analyze_col2 = st.columns([1, 4])
        with analyze_col1:
            analyze_clicked = st.button("Analyze Community", type="primary", use_container_width=True)
        with analyze_col2:
            st.caption("Uses Claude to synthesize Reddit discussions into strategic intelligence")

        if analyze_clicked:
            with st.spinner("Claude is analyzing community discussions..."):
                new_synthesis = _generate_reddit_ai_synthesis(reddit_df, top_posts_data, subreddit_stats)
                if new_synthesis:
                    insert_report(
                        report_type="reddit_synthesis",
                        title=f"Reddit Community Synthesis - {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                        content=new_synthesis,
                        articles_included=[],
                        stats={"total_posts": total_posts, "total_engagement": int(total_engagement)},
                    )
                    st.session_state["reddit_synthesis_content"] = new_synthesis
                    st.session_state["reddit_synthesis_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                else:
                    st.warning("AI analysis unavailable. Check API key. Showing standard analysis below.")
                st.rerun()

    st.markdown("---")

    # =========================================================================
    # STRUCTURED DATA SECTIONS (always shown)
    # =========================================================================

    # Subreddit context for richer descriptions
    subreddit_context = {
        "mlops": "ML deployment and operations",
        "dataengineering": "data infrastructure and pipelines",
        "devops": "DevOps practices for AI",
        "kubernetes": "K8s for ML workloads",
        "aws": "AWS AI services deployment",
        "googlecloud": "GCP AI deployment",
        "azure": "Azure AI implementation",
        "ExperiencedDevs": "senior engineer deployment stories",
        "cscareerquestions": "career incl. FDE/solutions roles",
        "consulting": "AI implementation consulting",
        "startups": "startup AI deployment",
        "SaaS": "SaaS AI product deployment",
        "ProductManagement": "AI product rollout",
        "enterpriseIT": "enterprise AI adoption",
        "LocalLLaMA": "self-hosted LLM deployment",
        "LangChain": "LLM app development",
        "ClaudeAI": "Claude enterprise use cases",
        "OpenAI": "OpenAI API production usage",
        "MachineLearning": "ML engineering practices",
        "datascience": "data science in production",
        "cursor": "AI coding tool deployment",
        "CursorAI": "Cursor enterprise workflows",
        "copilot": "Copilot enterprise rollout",
    }

    # Most Engaging Discussions
    st.markdown("##### Most Engaging Discussions")

    for i, (_, row) in enumerate(top_posts.head(7).iterrows(), 1):
        title = row.get("title", "")
        url = row.get("url", "")
        subreddit = row.get("subreddit", "")
        score = row.get("score", 0)
        comments = row.get("num_comments", 0)
        content = row.get("full_content", "") or ""
        summary = row.get("content_summary", "") or ""
        date_str = ""
        if "display_date" in row and pd.notna(row["display_date"]):
            date_str = row["display_date"].strftime("%b %d")

        # Clean content for excerpt
        if "[METADATA]" in content:
            content = content.split("[METADATA]")[0].strip()

        sub_desc = subreddit_context.get(subreddit, "")
        sub_info = f" ({sub_desc})" if sub_desc else ""

        st.markdown(
            f"**{i}. [{title}]({url})**\n"
            f"   *r/{subreddit}{sub_info} | {score:,} upvotes | {comments:,} comments{' | ' + date_str if date_str else ''}*"
        )

        # Show AI summary if available, otherwise excerpt
        if summary:
            st.markdown(f"   > {summary}")
        elif content and len(content) > 50:
            excerpt = content[:250].strip()
            if len(content) > 250:
                last_space = excerpt.rfind(' ')
                if last_space > 100:
                    excerpt = excerpt[:last_space] + "..."
                else:
                    excerpt += "..."
            st.markdown(f"   > {excerpt}")

    st.markdown("---")

    # Two columns layout: Activity + Topics
    col1, col2 = st.columns([2, 1])

    with col1:
        # Subreddit Activity Chart
        st.markdown("##### Subreddit Activity")
        sub_counts = subreddit_counts.head(12)
        if not sub_counts.empty:
            fig = px.bar(
                x=sub_counts.values,
                y=["r/" + s for s in sub_counts.index],
                orientation="h",
                labels={"x": "Posts", "y": "Subreddit"},
                color_discrete_sequence=["#818CF8"],
            )
            fig.update_layout(
                height=400,
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#475569"),
            )
            fig.update_xaxes(gridcolor="#F1F5F9")
            fig.update_yaxes(gridcolor="#F1F5F9")
            st.plotly_chart(fig, width="stretch")

    with col2:
        # Topic analysis
        st.markdown("##### Hot Topics")

        topic_keywords = {
            "Claude/Anthropic": ["claude", "anthropic", "sonnet", "opus"],
            "OpenAI/GPT": ["openai", "gpt", "chatgpt", "o1", "o3"],
            "Open Source": ["llama", "mistral", "deepseek", "open source", "local"],
            "Coding/Dev": ["code", "coding", "cursor", "copilot", "developer"],
            "Agents": ["agent", "agentic", "autonomous", "mcp"],
            "RAG/Data": ["rag", "retrieval", "vector", "embeddings", "pipeline"],
            "Deployment": ["deploy", "production", "serving", "inference", "mlops"],
            "Infrastructure": ["kubernetes", "docker", "aws", "azure", "gcp", "cloud"],
        }

        topic_counts = {}
        for topic, keywords in topic_keywords.items():
            count = 0
            for _, row in reddit_df.iterrows():
                title = (row.get("title") or "").lower()
                content_text = (row.get("content_summary") or "").lower()
                text = f"{title} {content_text}"
                if any(kw in text for kw in keywords):
                    count += 1
            if count > 0:
                topic_counts[topic] = count

        if topic_counts:
            sorted_topics = sorted(topic_counts.items(), key=lambda x: x[1], reverse=True)
            max_count = sorted_topics[0][1] if sorted_topics else 1
            for topic, count in sorted_topics[:8]:
                bar_width = int(count / max_count * 100)
                st.markdown(
                    f'<div style="margin-bottom:8px;">'
                    f'<div style="font-weight:600;font-size:13px;color:#334155;">{topic}</div>'
                    f'<div style="background:#E2E8F0;border-radius:4px;height:18px;width:100%;">'
                    f'<div style="background:#818CF8;border-radius:4px;height:18px;width:{bar_width}%;'
                    f'display:flex;align-items:center;padding-left:6px;">'
                    f'<span style="font-size:11px;color:white;font-weight:600;">{count}</span>'
                    f'</div></div></div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("No topics detected yet.")

    st.markdown("---")

    # Community Sentiment Analysis
    st.markdown("##### Community Sentiment")
    st.caption("Based on post titles, content, and engagement patterns")

    sentiment_keywords = {
        "positive": ["amazing", "great", "love", "best", "incredible", "impressive", "breakthrough", "excited", "finally", "awesome", "game changer"],
        "negative": ["terrible", "broken", "disappointed", "worse", "hate", "problem", "issue", "bug", "frustrated", "annoying", "useless"],
        "skeptical": ["overrated", "hype", "scam", "actually", "really", "doubt", "skeptical", "overhyped", "bubble"],
        "curious": ["anyone", "how", "why", "what", "?", "help", "question", "recommend", "suggest"],
    }

    sentiment_counts = {"Positive": 0, "Negative": 0, "Skeptical": 0, "Curious/Questions": 0, "Neutral": 0}

    for _, row in reddit_df.iterrows():
        title = (row.get("title") or "").lower()
        matched = False
        if any(kw in title for kw in sentiment_keywords["positive"]):
            sentiment_counts["Positive"] += 1
            matched = True
        if any(kw in title for kw in sentiment_keywords["negative"]):
            sentiment_counts["Negative"] += 1
            matched = True
        if any(kw in title for kw in sentiment_keywords["skeptical"]):
            sentiment_counts["Skeptical"] += 1
            matched = True
        if any(kw in title for kw in sentiment_keywords["curious"]):
            sentiment_counts["Curious/Questions"] += 1
            matched = True
        if not matched:
            sentiment_counts["Neutral"] += 1

    col1, col2 = st.columns(2)

    with col1:
        fig = px.pie(
            values=list(sentiment_counts.values()),
            names=list(sentiment_counts.keys()),
            color=list(sentiment_counts.keys()),
            color_discrete_map={
                "Positive": "#34D399",
                "Negative": "#F87171",
                "Skeptical": "#FBBF24",
                "Curious/Questions": "#60A5FA",
                "Neutral": "#CBD5E1",
            },
            hole=0.45,
        )
        fig.update_layout(
            height=300,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#475569"),
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        st.markdown("**Sentiment Breakdown:**")
        total = sum(sentiment_counts.values())
        for sentiment, count in sentiment_counts.items():
            pct = (count / total * 100) if total > 0 else 0
            color = {"Positive": "#34D399", "Negative": "#F87171", "Skeptical": "#FBBF24", "Curious/Questions": "#60A5FA", "Neutral": "#CBD5E1"}.get(sentiment, "#CBD5E1")
            st.markdown(f'<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:{color};margin-right:6px;"></span> **{sentiment}**: {count} ({pct:.1f}%)', unsafe_allow_html=True)

    st.markdown("---")

    # Top Discussions by Subreddit
    st.markdown("##### Browse by Community")

    subreddits = ["All"] + sorted(reddit_df["subreddit"].unique().tolist())
    selected_sub = st.selectbox("Select Subreddit", subreddits)

    if selected_sub != "All":
        display_df = reddit_df[reddit_df["subreddit"] == selected_sub]
    else:
        display_df = reddit_df

    display_df = display_df.sort_values("engagement", ascending=False)

    for _, row in display_df.head(15).iterrows():
        title = row.get("title", "")
        url = row.get("url", "")
        subreddit = row.get("subreddit", "")
        score = row.get("score", 0)
        comments = row.get("num_comments", 0)
        content = row.get("full_content", "") or ""
        summary = row.get("content_summary", "") or ""

        date_display = ""
        if "display_date" in row and pd.notna(row.get("display_date")):
            date_display = row["display_date"].strftime("%b %d, %Y")

        if "[METADATA]" in content:
            content = content.split("[METADATA]")[0].strip()

        with st.container():
            st.markdown(f"**[{title}]({url})**")
            caption_parts = [f"r/{subreddit}", f"{score} upvotes", f"{comments} comments"]
            if date_display:
                caption_parts.append(date_display)
            st.caption(" | ".join(caption_parts))

            # Show AI summary if available
            if summary:
                st.markdown(f"> {summary}")
            elif content and len(content) > 50:
                with st.expander("View excerpt"):
                    st.markdown(content[:500] + "..." if len(content) > 500 else content)

            st.markdown("---")

    # Discussion Trends (if enough data)
    st.markdown("##### Discussion Trends")

    if "discovered_date" in reddit_df.columns:
        reddit_df["parsed_date"] = pd.to_datetime(reddit_df["discovered_date"], errors="coerce")
        valid_dates_df = reddit_df[reddit_df["parsed_date"].notna()].copy()

        if not valid_dates_df.empty:
            valid_dates_df["date_str"] = valid_dates_df["parsed_date"].dt.strftime("%Y-%m-%d")
            daily_by_sub = valid_dates_df.groupby(["date_str", "subreddit"]).size().reset_index(name="count")

            if not daily_by_sub.empty and len(daily_by_sub["date_str"].unique()) > 1:
                top_subs = valid_dates_df["subreddit"].value_counts().head(5).index.tolist()
                daily_top = daily_by_sub[daily_by_sub["subreddit"].isin(top_subs)]

                if not daily_top.empty:
                    daily_top = daily_top.sort_values("date_str")
                    fig = px.line(
                        daily_top,
                        x="date_str",
                        y="count",
                        color="subreddit",
                        markers=True,
                        labels={"date_str": "Date", "count": "Posts", "subreddit": "Subreddit"},
                        color_discrete_sequence=px.colors.qualitative.Pastel,
                    )
                    fig.update_layout(
                        height=300,
                        xaxis_title="Date",
                        yaxis_title="Number of Posts",
                        xaxis=dict(tickangle=45),
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#475569"),
                    )
                    fig.update_xaxes(gridcolor="#F1F5F9")
                    fig.update_yaxes(gridcolor="#F1F5F9")
                    st.plotly_chart(fig, width="stretch")
                else:
                    st.info("Not enough data across multiple days to show trends.")
            else:
                st.info("Need data from multiple days to show discussion trends. Keep fetching content over time.")
        else:
            st.info("No valid date data available for trend analysis.")


def render_reports_page():
    """Render the reports page with persistent report storage."""
    st.markdown("## Research Reports")
    st.caption("Generate and view research reports — reports are saved and persist across sessions")
    st.markdown("")

    # Load saved reports from database on first visit
    if "reports_loaded" not in st.session_state:
        saved_reports = get_all_reports(limit=20, exclude_types=["reddit_synthesis"])
        st.session_state.saved_reports = saved_reports
        # Auto-load the most recent report if we don't have one showing
        if saved_reports and "current_report" not in st.session_state:
            st.session_state.current_report = saved_reports[0].get("content", "")
            st.session_state.current_report_title = saved_reports[0].get("title", "")
        st.session_state.reports_loaded = True

    # Compact controls at top
    col1, col2, col3 = st.columns([2, 2, 1])

    with col1:
        report_type = st.radio(
            "Report Type",
            ["Executive Briefing (1-2 pages)", "Weekly Link Report", "Daily Digest"],
            horizontal=True,
            label_visibility="collapsed",
        )

    with col2:
        st.caption("Executive = deep analysis | Weekly = all links | Daily = quick summary")

    with col3:
        generate_clicked = st.button("Generate", icon=":material/description:", type="primary", width="stretch")

    if generate_clicked:
        with st.spinner("Generating report... (this may take a moment for executive briefings)"):
            generator = ReportGenerator()
            if "Executive" in report_type:
                content = generator.generate_executive_briefing()
                rtype = "executive"
            elif "Weekly" in report_type:
                content = generator.generate_weekly_report()
                rtype = "weekly"
            else:
                content = generator.generate_daily_digest()
                rtype = "daily"

            filepath = generator.save_report(content, rtype)
            st.session_state.current_report = content
            st.session_state.current_report_title = f"{rtype.title()} Report - {datetime.now().strftime('%Y-%m-%d')}"
            # Refresh saved reports list
            st.session_state.saved_reports = get_all_reports(limit=20, exclude_types=["reddit_synthesis"])
            st.success(f"Report generated and saved!")

    st.markdown("---")

    # Report history sidebar + current report display
    report_col, history_col = st.columns([3, 1])

    with history_col:
        st.markdown("##### Saved Reports")
        saved_reports = st.session_state.get("saved_reports", [])
        if saved_reports:
            for i, report in enumerate(saved_reports[:10]):
                title = report.get("title", "Untitled")
                rtype = report.get("report_type", "")
                date = report.get("generated_date", "")[:10]
                type_icon = {"executive": "📊", "weekly": "📋", "daily": "📰"}.get(rtype, "📄")

                if st.button(
                    f"{type_icon} {date}",
                    key=f"report_{i}",
                    help=title,
                    use_container_width=True,
                ):
                    st.session_state.current_report = report.get("content", "")
                    st.session_state.current_report_title = title
                    st.rerun()
        else:
            st.caption("No saved reports yet. Generate one to get started.")

    with report_col:
        if "current_report" in st.session_state and st.session_state.current_report:
            report_title = st.session_state.get("current_report_title", "")
            if report_title:
                st.caption(f"*{report_title}*")
            st.markdown(st.session_state.current_report)
        else:
            st.info("Click 'Generate' to create a new research report, or select a saved report from the history panel. The Executive Briefing provides the deepest analysis with AI-powered narrative insights.")

    # Quick stats for report
    st.markdown("---")
    st.markdown("##### Report Data Summary")

    stats = get_database_stats()
    df = get_articles_df(limit=100)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Articles This Week", len(df) if not df.empty else 0)

    with col2:
        if not df.empty and "relevance_score" in df.columns:
            high_rel = len(df[df["relevance_score"] >= 0.6])
            st.metric("High Relevance", high_rel)
        else:
            st.metric("High Relevance", 0)

    with col3:
        st.metric("Total Insights", stats.get("total_insights", 0))

    with col4:
        st.metric("Saved Reports", len(st.session_state.get("saved_reports", [])))


def render_settings_page():
    """Render the settings page."""
    st.markdown("## Settings")
    st.caption("Configure the AI Deployment Monitor")
    st.markdown("")

    # API Status
    st.markdown("##### API Configuration")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Anthropic API**")
        if ANTHROPIC_API_KEY:
            st.success("✅ Configured")
        else:
            st.error("❌ Not configured - Add ANTHROPIC_API_KEY to .env")

    with col2:
        st.markdown("**Reddit API**")
        if REDDIT_CLIENT_ID:
            st.success("✅ Configured")
        else:
            st.warning("⚠️ Not configured - Reddit scraping disabled")

    st.markdown("---")

    # Database management
    st.markdown("##### Database Management")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Reinitialize Database", icon=":material/restart_alt:", width="stretch"):
            init_database()
            seed_sources()
            st.success("Database reinitialized!")

    with col2:
        stats = get_database_stats()
        st.metric("Database Size", f"{stats.get('total_articles', 0)} articles")

    with col3:
        st.metric("Reports Generated", stats.get("total_reports", 0))

    st.markdown("---")

    # Keyword configuration display
    st.markdown("##### Monitored Keywords")

    with st.expander("Primary Keywords (High Weight)"):
        st.write(", ".join(PRIMARY_KEYWORDS[:30]) + "...")

    with st.expander("Target Companies"):
        cols = st.columns(4)
        for i, company in enumerate(TARGET_COMPANIES):
            with cols[i % 4]:
                st.markdown(f"• {company.title()}")

    with st.expander("Trend Categories"):
        for category, keywords in TREND_CATEGORIES.items():
            st.markdown(f"**{category}:** {', '.join(keywords)}")

    st.markdown("---")

    # Manual scrape controls
    st.markdown("##### Manual Data Collection")

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Scrape Hacker News", width="stretch"):
            with st.spinner("Scraping HN..."):
                run_scraper(source="hackernews")
            st.success("Done!")
            st.rerun()

    with col2:
        if st.button("Scrape RSS Feeds", width="stretch"):
            with st.spinner("Scraping RSS..."):
                run_scraper(source="rss")
            st.success("Done!")
            st.rerun()

    with col3:
        if st.button("Scrape Reddit", width="stretch"):
            with st.spinner("Scraping Reddit..."):
                run_scraper(source="reddit")
            st.success("Done!")
            st.rerun()


def render_opportunities_page():
    """Render the Opportunities intelligence page."""
    st.markdown("## Opportunity Signals")
    st.caption("AI-detected business opportunities from deployment signals, funding, competitive shifts, and customer success")
    st.markdown("")

    # Top metrics
    try:
        opp_stats = get_opportunity_stats()
        total_signals = opp_stats.get("total_signals", 0)
    except Exception:
        opp_stats = {"total_signals": 0, "signal_types": {}, "verticals": {}, "top_companies": {}}
        total_signals = 0

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Signals", total_signals)
    with col2:
        st.metric("Signal Types", len(opp_stats.get("signal_types", {})))
    with col3:
        st.metric("Verticals", len(opp_stats.get("verticals", {})))
    with col4:
        st.metric("Companies Tracked", len(opp_stats.get("top_companies", {})))

    st.markdown("")

    # Signal type breakdown and industry verticals side by side
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("##### Signals by Type")
        signal_types = opp_stats.get("signal_types", {})
        if signal_types:
            type_labels = {
                "deployment_milestone": "Deployment Milestone",
                "funding": "Funding Round",
                "competitive_shift": "Competitive Shift",
                "competitive_signal": "Competitive Signal",
                "customer_success": "Customer Success",
                "hiring_wave": "Hiring Wave",
                "hiring_signal": "Hiring Signal",
                "product_launch": "Product Launch",
                "market_opening": "Market Opening",
                "business_impact": "Business Impact",
                "strategic_move": "Strategic Move",
            }
            display_types = {type_labels.get(k, k.replace("_", " ").title()): v for k, v in signal_types.items()}
            fig = px.bar(
                x=list(display_types.values()),
                y=list(display_types.keys()),
                orientation="h",
                labels={"x": "Count", "y": "Signal Type"},
                color_discrete_sequence=["#6366F1"],
            )
            fig.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#475569"),
            )
            fig.update_xaxes(gridcolor="#F1F5F9")
            fig.update_yaxes(gridcolor="#F1F5F9")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No opportunity signals yet. Run 'Analyze Articles' to detect them.")

    with col2:
        st.markdown("##### Industry Verticals")
        verticals = opp_stats.get("verticals", {})
        if verticals:
            fig = px.pie(
                values=list(verticals.values()),
                names=list(verticals.keys()),
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Pastel,
            )
            fig.update_layout(
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#475569"),
            )
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No industry vertical data yet.")

    st.markdown("---")

    # Recent opportunity signals
    st.markdown("##### Latest Opportunity Signals")

    try:
        signals = get_recent_opportunity_signals(limit=30)
    except Exception:
        signals = []

    if signals:
        for signal in signals:
            signal_type = signal.get("signal_type", "unknown")
            strength = signal.get("signal_strength", 0)
            title = signal.get("article_title", "")
            url = signal.get("article_url", "")
            company = signal.get("company_name")
            vertical = signal.get("industry_vertical")
            stage = signal.get("deployment_stage")
            summary = signal.get("opportunity_summary", "")
            source = signal.get("source_name", "")

            # Signal type badge colors
            type_colors = {
                "deployment_milestone": "#059669",
                "funding": "#7C3AED",
                "competitive_signal": "#DC2626",
                "competitive_shift": "#DC2626",
                "customer_success": "#2563EB",
                "hiring_signal": "#D97706",
                "hiring_wave": "#D97706",
                "product_launch": "#0891B2",
                "market_opening": "#059669",
                "business_impact": "#0D9488",
                "strategic_move": "#7C3AED",
            }
            badge_color = type_colors.get(signal_type, "#6B7280")
            display_type = signal_type.replace("_", " ").title()

            with st.container():
                col1, col2 = st.columns([0.85, 0.15])
                with col1:
                    badge_html = f'<span style="background:{badge_color};color:white;padding:2px 8px;border-radius:4px;font-size:0.7rem;font-weight:600;">{display_type}</span>'
                    if company:
                        badge_html += f' <span style="background:#F1F5F9;color:#334155;padding:2px 8px;border-radius:4px;font-size:0.7rem;">{company}</span>'
                    if vertical:
                        badge_html += f' <span style="background:#FFF7ED;color:#C2410C;padding:2px 8px;border-radius:4px;font-size:0.7rem;">{vertical}</span>'
                    if stage:
                        badge_html += f' <span style="background:#EEF2FF;color:#4338CA;padding:2px 8px;border-radius:4px;font-size:0.7rem;">{stage}</span>'
                    st.markdown(badge_html, unsafe_allow_html=True)
                    st.markdown(f"**[{title}]({url})**")
                    if summary:
                        st.caption(summary)

                with col2:
                    strength_pct = int(strength * 100)
                    st.markdown(f"<div style='text-align:right;font-size:1.2rem;font-weight:600;color:{badge_color};'>{strength_pct}%</div>", unsafe_allow_html=True)
                    st.caption(f"{source}")

                st.markdown("---")
    else:
        st.info("No opportunity signals detected yet. Click 'Analyze Articles' in the sidebar to start extracting signals from your collected content.")

    st.markdown("---")

    # Company Intelligence section
    st.markdown("##### Company Intelligence")
    st.caption("Companies tracked across all sources, ranked by activity")

    try:
        companies = get_company_intel(limit=15)
    except Exception:
        companies = []

    if companies:
        company_data = []
        for c in companies:
            company_data.append({
                "Company": c.get("company_name", ""),
                "Industry": c.get("industry", "-") or "-",
                "Mentions": c.get("total_mentions", 0),
                "Latest Signal": (c.get("latest_signal_type") or "-").replace("_", " ").title(),
                "Stage": (c.get("latest_deployment_stage") or "-").title(),
                "Last Seen": str(c.get("last_seen", ""))[:10],
            })
        st.dataframe(pd.DataFrame(company_data), width="stretch", hide_index=True)
    else:
        st.info("No company intelligence yet. Run analysis to start tracking companies.")


def render_article_card(article, expanded=True):
    """Render a single article card."""
    if isinstance(article, pd.Series):
        article = article.to_dict()

    title = article.get("title", "Untitled")
    url = article.get("url", "#")
    source = article.get("source_name", "Unknown")
    score = article.get("relevance_score", 0)
    summary = article.get("content_summary", "")
    category = article.get("category", "")

    # Get and format date
    date_display = ""
    discovered_date = article.get("discovered_date")
    if discovered_date:
        try:
            if isinstance(discovered_date, str):
                dt = pd.to_datetime(discovered_date)
            else:
                dt = discovered_date
            date_display = dt.strftime("%b %d, %Y")
        except:
            pass

    # Score color
    if score >= 0.7:
        score_class = "score-high"
    elif score >= 0.5:
        score_class = "score-medium"
    else:
        score_class = "score-low"

    with st.container():
        col1, col2 = st.columns([0.9, 0.1])

        with col1:
            st.markdown(f"**[{title}]({url})**")
            caption_parts = [f"{source}"]
            if category:
                caption_parts.append(f"{category}")
            if date_display:
                caption_parts.append(f"{date_display}")
            st.caption(" | ".join(caption_parts))

        with col2:
            st.markdown(f"<span class='{score_class}'>{score:.2f}</span>", unsafe_allow_html=True)

        if expanded and summary:
            st.markdown(f"_{summary}_")

        # Keywords/companies as tags
        keywords = article.get("keywords_matched", "[]")
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except:
                keywords = []

        if keywords:
            tag_html = " ".join([f'<span class="trend-tag">{k}</span>' for k in keywords[:5]])
            st.markdown(tag_html, unsafe_allow_html=True)

        st.markdown("---")


def count_companies(df):
    """Count company mentions across articles."""
    company_counts = Counter()

    for _, row in df.iterrows():
        keywords = row.get("keywords_matched") or "[]"
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except:
                keywords = []

        if not keywords:
            keywords = []

        title = (row.get("title") or "").lower()
        summary = (row.get("content_summary") or "").lower()
        text = f"{title} {summary}"

        for company in TARGET_COMPANIES:
            if company.lower() in [k.lower() for k in keywords if k] or company.lower() in text:
                company_counts[company.title()] += 1

    return dict(company_counts.most_common(15))


def analyze_trends(df):
    """Analyze trend categories in articles."""
    trend_counts = {}

    for category, keywords in TREND_CATEGORIES.items():
        count = 0
        for _, row in df.iterrows():
            title = (row.get("title") or "").lower()
            summary = (row.get("content_summary") or "").lower()
            content = (row.get("full_content") or "").lower()
            text = f"{title} {summary} {content}"

            if any(kw.lower() in text for kw in keywords):
                count += 1

        trend_counts[category] = count

    return trend_counts


def filter_by_keywords(df, keywords):
    """Filter DataFrame by keywords."""
    mask = df.apply(
        lambda row: any(
            kw.lower() in (str(row.get("title", "")) + str(row.get("content_summary", ""))).lower()
            for kw in keywords
        ),
        axis=1
    )
    return df[mask]


def run_scraper(source="all"):
    """Run the content scraper."""
    scorer = RelevanceScorer()

    scrapers = []
    if source in ["hackernews", "all"]:
        scrapers.append(("hackernews", HackerNewsScraper(items_to_fetch=50)))
    if source in ["rss", "all"]:
        scrapers.append(("rss", RSSFeedScraper(max_entries_per_feed=10)))
    if source in ["reddit", "all"]:
        scrapers.append(("reddit", RedditScraper(posts_per_subreddit=15)))

    total_new = 0

    for source_type, scraper in scrapers:
        try:
            items = scraper.run()
            db_sources = get_source_by_type(source_type)
            source_id_map = {s["name"]: s["id"] for s in db_sources}

            for item in items:
                # Calculate engagement from metadata (Reddit upvotes + comments, etc.)
                engagement = item.get("score", 0) + item.get("num_comments", 0)
                relevance = scorer.score(
                    item.get("title", ""),
                    item.get("content", ""),
                    engagement=engagement,
                )

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
                    total_new += 1

            for db_source in db_sources:
                update_source_last_checked(db_source["id"])

        except Exception as e:
            st.error(f"Error scraping {source_type}: {e}")

    return total_new


def run_analysis(limit=30):
    """Run Claude analysis on unprocessed articles with opportunity extraction."""
    articles = get_unprocessed_articles(limit=limit)
    if not articles:
        return 0

    scorer = RelevanceScorer()
    analyzer = ContentAnalyzer()

    analyzed = 0

    for article in articles:
        relevance = scorer.score(
            article["title"],
            article.get("full_content", ""),
        )

        summary = ""
        sentiment = "neutral"
        category = "news"

        if analyzer.client and relevance["score"] >= RELEVANCE_THRESHOLD:
            analysis = analyzer.analyze(
                article["title"],
                article.get("full_content", ""),
                article.get("url", ""),
            )
            if analysis:
                summary = analysis.get("summary", "")
                sentiment = analysis.get("sentiment", "neutral")
                category = analysis.get("category", "news")

                # Extract and store opportunity signals
                opp = analysis.get("opportunity_signals", {})
                opp_type = opp.get("signal_type", "none")
                opp_strength = opp.get("signal_strength", 0.0)

                if opp_type and opp_type != "none" and opp_strength > 0.2:
                    deploy_ctx = analysis.get("deployment_context", {})
                    company = deploy_ctx.get("deploying_company")
                    vertical = analysis.get("industry_vertical", "unknown")
                    stage = deploy_ctx.get("deployment_stage")

                    try:
                        insert_opportunity_signal(
                            article_id=article["id"],
                            signal_type=opp_type,
                            signal_strength=opp_strength,
                            company_name=company,
                            industry_vertical=vertical if vertical != "unknown" else None,
                            deployment_stage=stage,
                            business_impact=deploy_ctx.get("business_impact"),
                            opportunity_summary=opp.get("opportunity_summary"),
                            keywords_triggered=relevance.get("opportunity_matches", []),
                        )
                    except Exception as e:
                        pass  # Non-critical

                    # Update company intelligence
                    if company:
                        try:
                            comp_intel = analysis.get("competitive_intel", {})
                            upsert_company_intel(
                                company_name=company,
                                industry=vertical if vertical != "unknown" else None,
                                size_tier=deploy_ctx.get("company_size"),
                                signal_type=opp_type,
                                deployment_stage=stage,
                                technologies=[deploy_ctx.get("model_or_tool")] if deploy_ctx.get("model_or_tool") else None,
                            )
                        except Exception:
                            pass

                    # Update article opportunity fields
                    try:
                        update_article_opportunity(
                            article_id=article["id"],
                            opportunity_score=opp_strength,
                            opportunity_types=[opp_type],
                            industry_vertical=vertical if vertical != "unknown" else None,
                            deployment_stage=stage,
                        )
                    except Exception:
                        pass

                # Also store company intel for companies mentioned even without strong opp signal
                for comp in analysis.get("companies_mentioned", []):
                    if comp and len(comp) > 1:
                        try:
                            upsert_company_intel(company_name=comp)
                        except Exception:
                            pass

        # Also store keyword-detected opportunity signals (no Claude needed)
        if relevance.get("opportunity_types"):
            try:
                update_article_opportunity(
                    article_id=article["id"],
                    opportunity_score=relevance.get("opportunity_score", 0.0),
                    opportunity_types=relevance["opportunity_types"],
                    industry_vertical=relevance["industry_verticals"][0] if relevance.get("industry_verticals") else None,
                )
            except Exception:
                pass

        update_article_analysis(
            article_id=article["id"],
            summary=summary,
            sentiment=sentiment,
            category=category,
            relevance_score=relevance["score"],
            keywords_matched=relevance["all_keywords_matched"],
        )

        analyzed += 1
        time.sleep(0.1)

    return analyzed


# Main app
def main():
    init_session_state()
    page = render_sidebar()

    if page == "Dashboard":
        render_dashboard()
    elif page == "Opportunities":
        render_opportunities_page()
    elif page == "Articles":
        render_articles_page()
    elif page == "Search":
        render_search_page()
    elif page == "Trends":
        render_trends_page()
    elif page == "Reddit Pulse":
        render_reddit_pulse_page()
    elif page == "Reports":
        render_reports_page()
    elif page == "Settings":
        render_settings_page()


if __name__ == "__main__":
    main()
