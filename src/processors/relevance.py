"""Relevance scoring for content filtering with opportunity signal detection."""

import re
import logging
from typing import List, Dict, Tuple, Any

from ..config import (
    PRIMARY_KEYWORDS,
    SECONDARY_KEYWORDS,
    TARGET_COMPANIES,
    EXCLUSION_KEYWORDS,
    RELEVANCE_THRESHOLD,
    OPPORTUNITY_KEYWORDS,
    INDUSTRY_VERTICALS,
)

logger = logging.getLogger(__name__)


class RelevanceScorer:
    """Score content relevance based on keyword matching and opportunity signals."""

    def __init__(
        self,
        primary_keywords: List[str] = None,
        secondary_keywords: List[str] = None,
        target_companies: List[str] = None,
        exclusion_keywords: List[str] = None,
        opportunity_keywords: List[str] = None,
    ):
        self.primary_keywords = [k.lower() for k in (primary_keywords or PRIMARY_KEYWORDS)]
        self.secondary_keywords = [k.lower() for k in (secondary_keywords or SECONDARY_KEYWORDS)]
        self.target_companies = [c.lower() for c in (target_companies or TARGET_COMPANIES)]
        self.exclusion_keywords = [e.lower() for e in (exclusion_keywords or EXCLUSION_KEYWORDS)]
        self.opportunity_keywords = [o.lower() for o in (opportunity_keywords or OPPORTUNITY_KEYWORDS)]

        # Weights for scoring
        self.primary_weight = 0.15  # Per match
        self.secondary_weight = 0.08  # Per match
        self.company_weight = 0.20  # Per company mentioned
        self.exclusion_penalty = 0.30  # Per exclusion keyword
        self.opportunity_weight = 0.12  # Per opportunity signal
        self.title_bonus = 1.5  # Multiplier for title matches

    def _normalize_text(self, text: str) -> str:
        """Normalize text for matching."""
        if not text:
            return ""
        text = text.lower()
        text = re.sub(r"\s+", " ", text)
        return text

    def _find_keyword_matches(
        self,
        text: str,
        keywords: List[str],
    ) -> List[str]:
        """Find all matching keywords in text."""
        matches = []
        for keyword in keywords:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text):
                matches.append(keyword)
        return matches

    def _find_company_mentions(self, text: str) -> List[str]:
        """Find all mentioned target companies."""
        mentions = []
        for company in self.target_companies:
            pattern = r"\b" + re.escape(company) + r"\b"
            if re.search(pattern, text):
                mentions.append(company)
        return mentions

    def _has_exclusion_keywords(self, text: str) -> Tuple[bool, List[str]]:
        """Check for exclusion keywords."""
        found = []
        for keyword in self.exclusion_keywords:
            pattern = r"\b" + re.escape(keyword) + r"\b"
            if re.search(pattern, text):
                found.append(keyword)
        return len(found) > 0, found

    def _detect_industry_verticals(self, text: str) -> List[str]:
        """Detect which industry verticals are mentioned."""
        verticals = []
        for vertical, keywords in INDUSTRY_VERTICALS.items():
            for kw in keywords:
                pattern = r"\b" + re.escape(kw) + r"\b"
                if re.search(pattern, text):
                    verticals.append(vertical)
                    break
        return verticals

    def _classify_opportunity_type(self, opp_matches: List[str]) -> List[str]:
        """Classify opportunity signals into types."""
        types = set()
        for match in opp_matches:
            if match in ["raised $", "series a", "series b", "series c", "series d",
                         "seed round", "funding round", "ipo", "strategic investment"]:
                types.add("funding")
            elif match in ["acquisition", "acquires", "joint venture", "strategic partnership"]:
                types.add("strategic_move")
            elif match in ["pilot program", "general availability", "rolled out to",
                           "production deployment", "now available", "launched",
                           "went live", "ga release", "public preview",
                           "expanding to", "scaling to"]:
                types.add("deployment_milestone")
            elif match in ["reduced costs by", "saved hours", "roi of",
                           "increased revenue", "cost savings", "productivity gains",
                           "time savings", "efficiency gains",
                           "million in revenue", "billion valuation"]:
                types.add("business_impact")
            elif match in ["chose over", "switched from", "replaced", "migrated to",
                           "compared to", "alternative to", "better than", "instead of"]:
                types.add("competitive_signal")
            elif match in ["hiring for", "growing the team", "looking for engineers",
                           "new roles", "head of ai", "vp of engineering",
                           "founding engineer"]:
                types.add("hiring_signal")
            elif match in ["case study", "customer story", "success story",
                           "testimonial", "deployed by", "used by",
                           "adopted by", "implemented at"]:
                types.add("customer_success")
        return list(types)

    def score(
        self,
        title: str,
        content: str = "",
        engagement: int = 0,
    ) -> Dict[str, Any]:
        """
        Calculate relevance score with opportunity signal detection.

        Args:
            title: Article title
            content: Article content
            engagement: Community engagement score (upvotes + comments)

        Returns:
            Dictionary containing relevance and opportunity data.
        """
        # Normalize text
        title_text = self._normalize_text(title)
        full_text = self._normalize_text(f"{title} {content}")

        # Find matches
        primary_matches = self._find_keyword_matches(full_text, self.primary_keywords)
        secondary_matches = self._find_keyword_matches(full_text, self.secondary_keywords)
        companies = self._find_company_mentions(full_text)
        has_exclusions, exclusions = self._has_exclusion_keywords(full_text)

        # Opportunity signal detection
        opportunity_matches = self._find_keyword_matches(full_text, self.opportunity_keywords)
        opportunity_types = self._classify_opportunity_type(opportunity_matches)
        industry_verticals = self._detect_industry_verticals(full_text)

        # Title match bonus: keywords in title are worth more
        title_primary = self._find_keyword_matches(title_text, self.primary_keywords)
        title_companies = self._find_company_mentions(title_text)

        # Calculate base score
        score = 0.0

        # Primary keywords (max contribution: 0.45)
        score += min(len(primary_matches) * self.primary_weight, 0.45)

        # Title bonus for primary keywords
        score += min(len(title_primary) * self.primary_weight * (self.title_bonus - 1), 0.15)

        # Secondary keywords (max contribution: 0.24)
        score += min(len(secondary_matches) * self.secondary_weight, 0.24)

        # Company mentions (max contribution: 0.40)
        score += min(len(companies) * self.company_weight, 0.40)

        # Title bonus for company mentions
        score += min(len(title_companies) * self.company_weight * (self.title_bonus - 1), 0.10)

        # Opportunity signal bonus (max contribution: 0.25)
        opp_score = min(len(opportunity_matches) * self.opportunity_weight, 0.25)
        score += opp_score

        # Engagement bonus (logarithmic scale, max 0.10)
        if engagement > 0:
            import math
            engagement_bonus = min(math.log10(max(engagement, 1)) * 0.03, 0.10)
            score += engagement_bonus

        # Apply exclusion penalty
        if has_exclusions:
            if len(primary_matches) == 0 and len(companies) == 0:
                score -= len(exclusions) * self.exclusion_penalty

        # Ensure score is in valid range
        score = max(0.0, min(1.0, score))

        # Determine relevance
        is_relevant = score >= RELEVANCE_THRESHOLD

        return {
            "score": round(score, 3),
            "primary_matches": primary_matches,
            "secondary_matches": secondary_matches,
            "companies_mentioned": companies,
            "exclusions_found": exclusions,
            "is_relevant": is_relevant,
            "all_keywords_matched": primary_matches + secondary_matches,
            # Opportunity fields
            "opportunity_matches": opportunity_matches,
            "opportunity_score": round(opp_score, 3),
            "opportunity_types": opportunity_types,
            "industry_verticals": industry_verticals,
        }

    def quick_filter(self, title: str, content: str = "") -> bool:
        """
        Quick relevance check without full scoring.
        Use for initial filtering before more expensive analysis.
        """
        full_text = self._normalize_text(f"{title} {content}")

        # Check for any primary keyword
        for keyword in self.primary_keywords:
            if keyword in full_text:
                return True

        # Check for any target company
        for company in self.target_companies:
            if company in full_text:
                return True

        # Check for any opportunity keyword
        for keyword in self.opportunity_keywords:
            if keyword in full_text:
                return True

        # Check for multiple secondary keywords
        secondary_count = sum(1 for k in self.secondary_keywords if k in full_text)
        if secondary_count >= 2:
            return True

        return False

    def batch_score(self, items: List[Dict]) -> List[Dict]:
        """Score a batch of items and return with scores attached."""
        scored_items = []
        for item in items:
            title = item.get("title", "")
            content = item.get("content", "")
            engagement = item.get("engagement", 0)
            relevance = self.score(title, content, engagement)
            item["relevance"] = relevance
            scored_items.append(item)

        scored_items.sort(key=lambda x: x["relevance"]["score"], reverse=True)
        return scored_items

    def filter_relevant(
        self,
        items: List[Dict],
        threshold: float = None,
    ) -> List[Dict]:
        """Filter items to only those meeting relevance threshold."""
        threshold = threshold or RELEVANCE_THRESHOLD
        scored = self.batch_score(items)
        return [item for item in scored if item["relevance"]["score"] >= threshold]
