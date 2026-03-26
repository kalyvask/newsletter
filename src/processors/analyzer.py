"""Content analysis using Claude API."""

import json
import logging
from typing import List, Dict, Any, Optional

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from ..config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    MAX_TOKENS_SUMMARY,
    MAX_TOKENS_ANALYSIS,
    TARGET_COMPANIES,
)

logger = logging.getLogger(__name__)


class ContentAnalyzer:
    """Analyze content using Claude API."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or ANTHROPIC_API_KEY
        self.client = None
        self.total_tokens_used = 0

        if ANTHROPIC_AVAILABLE and self.api_key:
            try:
                self.client = anthropic.Anthropic(api_key=self.api_key)
                logger.info("Anthropic client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Anthropic client: {e}")
        else:
            logger.warning(
                "Anthropic client not available. Set ANTHROPIC_API_KEY in .env file."
            )

    def _call_claude(
        self,
        prompt: str,
        system: str = None,
        max_tokens: int = MAX_TOKENS_SUMMARY,
    ) -> Optional[str]:
        """Make a call to Claude API."""
        if not self.client:
            return None

        try:
            messages = [{"role": "user", "content": prompt}]

            kwargs = {
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system

            response = self.client.messages.create(**kwargs)

            # Track token usage
            if hasattr(response, "usage"):
                self.total_tokens_used += (
                    response.usage.input_tokens + response.usage.output_tokens
                )

            return response.content[0].text

        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return None

    def summarize(self, title: str, content: str, url: str = "") -> Optional[str]:
        """Generate a concise summary of the content."""
        if not content or len(content.strip()) < 50:
            return None

        system = """You are a research analyst summarizing articles about AI deployment
and enterprise AI adoption. Create clear, informative summaries focused on actionable
insights for someone studying AI implementation strategies."""

        prompt = f"""Summarize the following article in 2-3 sentences. Focus on:
- The main topic or argument
- Key insights about AI deployment, implementation, or adoption
- Any notable data points, metrics, or case studies mentioned

Title: {title}
URL: {url}

Content:
{content[:3000]}

Provide only the summary, no preamble or labels."""

        return self._call_claude(prompt, system, MAX_TOKENS_SUMMARY)

    def _parse_json_response(self, response: str) -> Optional[Dict]:
        """Parse a JSON response from Claude, handling common formatting issues."""
        if not response:
            return None
        try:
            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
            response = response.strip()
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Claude response as JSON: {e}")
            logger.debug(f"Response was: {response[:500]}")
            return None

    def analyze(self, title: str, content: str, url: str = "") -> Optional[Dict[str, Any]]:
        """
        Perform comprehensive analysis with opportunity signal extraction.

        Returns structured intelligence including business opportunity signals,
        deployment context, competitive intelligence, and industry classification.
        """
        if not self.client:
            logger.warning("Claude client not available for analysis")
            return None

        if not content or len(content.strip()) < 100:
            return None

        system = """You are a senior AI industry analyst at a top strategy consulting firm.
Your job is to extract actionable business intelligence from articles about AI deployment.
Focus on identifying concrete opportunities: who is deploying, what they built, how far along
they are, what business impact they achieved, and what this means for the competitive landscape.
Be precise, data-driven, and focus on signals that would help someone identify future opportunities."""

        companies_list = ", ".join(TARGET_COMPANIES[:20])

        prompt = f"""Analyze this article and extract structured business intelligence.

Title: {title}
URL: {url}
Content:
{content[:5000]}

Respond with a JSON object:
{{
    "summary": "3-4 sentence summary: WHO is doing WHAT with AI, HOW far along, and WHY it matters",
    "sentiment": "positive|neutral|negative|mixed",
    "category": "deployment_model|metrics|case_study|trend|opinion|news|technical|funding|product_launch",

    "opportunity_signals": {{
        "signal_type": "deployment_milestone|funding|competitive_shift|customer_success|hiring_wave|product_launch|market_opening|none",
        "signal_strength": 0.0-1.0,
        "opportunity_summary": "one sentence: what is the opportunity here?"
    }},

    "deployment_context": {{
        "deploying_company": "company name deploying AI (or null)",
        "company_size": "startup|scaleup|mid_market|enterprise|unknown",
        "deployment_stage": "announced|pilot|production|scaling|unknown",
        "use_case": "what the AI is being used for (or null)",
        "model_or_tool": "which AI model/tool/platform (or null)",
        "business_impact": "quantified impact if mentioned: cost savings, revenue, time saved (or null)"
    }},

    "competitive_intel": {{
        "competitive_choice": "why they chose this over alternatives (or null)",
        "displaced_product": "what product/approach was replaced (or null)",
        "switching_signal": true/false
    }},

    "industry_vertical": "financial_services|healthcare|legal|manufacturing|retail|education|government|media|professional_services|technology|cross_industry|unknown",

    "key_insights": [
        {{
            "text": "specific actionable insight",
            "type": "metric|best_practice|case_study|trend|competitive|risk|opportunity"
        }}
    ],

    "companies_mentioned": ["companies mentioned, especially: {companies_list}"],
    "relevance_notes": "why this matters for someone identifying AI deployment opportunities"
}}

Respond ONLY with valid JSON, no markdown or explanation."""

        response = self._call_claude(prompt, system, MAX_TOKENS_ANALYSIS)
        analysis = self._parse_json_response(response)

        if not analysis:
            return None

        # Validate required fields
        for field in ["summary", "sentiment", "category"]:
            if field not in analysis:
                analysis[field] = "unknown"

        # Ensure nested structures exist with defaults
        if "opportunity_signals" not in analysis:
            analysis["opportunity_signals"] = {
                "signal_type": "none",
                "signal_strength": 0.0,
                "opportunity_summary": None,
            }
        if "deployment_context" not in analysis:
            analysis["deployment_context"] = {}
        if "competitive_intel" not in analysis:
            analysis["competitive_intel"] = {}
        if "key_insights" not in analysis:
            analysis["key_insights"] = []
        if "companies_mentioned" not in analysis:
            analysis["companies_mentioned"] = []
        if "industry_vertical" not in analysis:
            analysis["industry_vertical"] = "unknown"

        return analysis

    def assess_relevance(self, title: str, content: str) -> Optional[Dict[str, Any]]:
        """
        Use Claude to assess relevance for borderline cases.
        More expensive but more accurate than keyword matching.
        """
        if not self.client:
            return None

        system = """You assess content relevance for AI deployment research.
Be precise and objective in your assessment."""

        prompt = f"""Assess whether this content is relevant for research on AI deployment strategies.

Title: {title}
Content: {content[:2000]}

Consider these topics as highly relevant:
- AI/ML deployment models and strategies
- Forward deployment / field engineering
- Enterprise AI adoption patterns
- AI implementation case studies
- AI success metrics and ROI
- Customer success for AI products
- Design partners and pilots

Rate relevance from 0.0 to 1.0 and explain briefly.

Respond with JSON:
{{
    "relevance_score": 0.0-1.0,
    "is_relevant": true/false,
    "reason": "brief explanation",
    "topics_matched": ["list of relevant topics found"]
}}

Respond ONLY with valid JSON."""

        response = self._call_claude(prompt, system, 300)
        return self._parse_json_response(response)

    def extract_insights(
        self,
        title: str,
        content: str,
    ) -> List[Dict[str, Any]]:
        """Extract specific insights from content."""
        analysis = self.analyze(title, content)
        if analysis and "key_insights" in analysis:
            return analysis["key_insights"]
        return []

    def batch_summarize(
        self,
        items: List[Dict],
        max_items: int = 20,
    ) -> List[Dict]:
        """
        Generate summaries for a batch of items.

        Args:
            items: List of dicts with 'title', 'content', and 'url' keys
            max_items: Maximum items to process (for cost control)

        Returns:
            Items with 'summary' key added
        """
        for i, item in enumerate(items[:max_items]):
            if i > 0 and i % 10 == 0:
                logger.info(f"Processed {i}/{min(len(items), max_items)} items")

            summary = self.summarize(
                item.get("title", ""),
                item.get("content", ""),
                item.get("url", ""),
            )
            item["summary"] = summary

        return items

    def get_token_usage(self) -> int:
        """Get total tokens used in this session."""
        return self.total_tokens_used
