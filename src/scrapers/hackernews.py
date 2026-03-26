"""Hacker News scraper using the official API."""

import logging
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional

from .base import BaseScraper
from ..config import HN_API_BASE, HN_ITEMS_PER_FETCH, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


class HackerNewsScraper(BaseScraper):
    """Scraper for Hacker News stories and comments."""

    def __init__(self, items_to_fetch: int = HN_ITEMS_PER_FETCH):
        super().__init__("Hacker News", "hackernews")
        self.items_to_fetch = items_to_fetch
        self.api_base = HN_API_BASE

    def _get_item(self, item_id: int) -> Optional[Dict]:
        """Fetch a single item from the HN API."""
        try:
            self.rate_limit()
            response = requests.get(
                f"{self.api_base}/item/{item_id}.json",
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.debug(f"Error fetching HN item {item_id}: {e}")
            return None

    def _get_top_comments(self, story: Dict, max_comments: int = 5) -> List[str]:
        """Get top-level comments for a story."""
        comments = []
        kid_ids = story.get("kids", [])[:max_comments]

        for kid_id in kid_ids:
            comment = self._get_item(kid_id)
            if comment and comment.get("text"):
                # Strip HTML tags (simple approach)
                text = comment["text"]
                for tag in ["<p>", "</p>", "<i>", "</i>", "<b>", "</b>", "<a>", "</a>"]:
                    text = text.replace(tag, " ")
                comments.append(text[:500])

        return comments

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch top and new stories from Hacker News."""
        results = []
        seen_urls = set()

        # Fetch from different story types
        story_types = ["topstories", "newstories", "beststories"]

        for story_type in story_types:
            try:
                self.rate_limit()
                response = requests.get(
                    f"{self.api_base}/{story_type}.json",
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                story_ids = response.json()[:self.items_to_fetch // len(story_types)]

                for story_id in story_ids:
                    story = self._get_item(story_id)
                    if not story or story.get("type") != "story":
                        continue

                    url = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")

                    # Skip duplicates
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    # Get top comments for context
                    top_comments = self._get_top_comments(story) if story.get("descendants", 0) > 0 else []

                    results.append({
                        "title": story.get("title", ""),
                        "url": url,
                        "author": story.get("by"),
                        "published_date": datetime.utcfromtimestamp(story.get("time", 0)),
                        "content": story.get("text", ""),  # For "Ask HN" posts
                        "metadata": {
                            "hn_id": story_id,
                            "score": story.get("score", 0),
                            "num_comments": story.get("descendants", 0),
                            "story_type": story_type.replace("stories", ""),
                            "is_show_hn": story.get("title", "").lower().startswith("show hn"),
                            "is_ask_hn": story.get("title", "").lower().startswith("ask hn"),
                            "top_comments": top_comments,
                            "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
                        },
                    })

                logger.info(f"Fetched stories from HN {story_type}")

            except Exception as e:
                logger.error(f"Error fetching HN {story_type}: {e}")
                continue

        return results

    def fetch_show_hn(self) -> List[Dict[str, Any]]:
        """Fetch Show HN posts specifically."""
        try:
            self.rate_limit()
            response = requests.get(
                f"{self.api_base}/showstories.json",
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            story_ids = response.json()[:self.items_to_fetch]

            results = []
            for story_id in story_ids:
                story = self._get_item(story_id)
                if story:
                    url = story.get("url", f"https://news.ycombinator.com/item?id={story_id}")
                    results.append({
                        "title": story.get("title", ""),
                        "url": url,
                        "author": story.get("by"),
                        "published_date": datetime.utcfromtimestamp(story.get("time", 0)),
                        "content": story.get("text", ""),
                        "metadata": {
                            "hn_id": story_id,
                            "score": story.get("score", 0),
                            "num_comments": story.get("descendants", 0),
                            "is_show_hn": True,
                            "hn_url": f"https://news.ycombinator.com/item?id={story_id}",
                        },
                    })

            return results
        except Exception as e:
            logger.error(f"Error fetching Show HN: {e}")
            return []
