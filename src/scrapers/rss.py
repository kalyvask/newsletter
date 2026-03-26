"""RSS feed scraper using feedparser."""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from time import mktime

import feedparser
import requests
from bs4 import BeautifulSoup

from .base import BaseScraper
from ..config import RSS_FEEDS, REQUEST_TIMEOUT

logger = logging.getLogger(__name__)


class RSSFeedScraper(BaseScraper):
    """Scraper for RSS/Atom feeds."""

    def __init__(
        self,
        feeds: Optional[Dict[str, str]] = None,
        max_entries_per_feed: int = 20,
    ):
        super().__init__("RSS Feeds", "rss")
        self.feeds = feeds or RSS_FEEDS
        self.max_entries_per_feed = max_entries_per_feed

    def _parse_date(self, entry: Dict) -> Optional[datetime]:
        """Parse publication date from feed entry."""
        for date_field in ["published_parsed", "updated_parsed", "created_parsed"]:
            if hasattr(entry, date_field) and getattr(entry, date_field):
                try:
                    return datetime.fromtimestamp(
                        mktime(getattr(entry, date_field))
                    )
                except Exception:
                    continue
        return None

    def _extract_content(self, entry: Dict) -> str:
        """Extract content from feed entry."""
        content = ""

        # Try different content fields
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            content = entry.summary
        elif hasattr(entry, "description"):
            content = entry.description

        # Strip HTML tags
        if content:
            soup = BeautifulSoup(content, "html.parser")
            content = soup.get_text(separator=" ", strip=True)

        return content[:3000]  # Limit content length

    def _fetch_feed(self, feed_name: str, feed_url: str) -> List[Dict]:
        """Fetch and parse a single RSS feed."""
        results = []

        try:
            self.rate_limit()

            # Use requests to fetch with timeout, then parse
            response = requests.get(
                feed_url,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "AIDeploymentMonitor/1.0"},
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo and not feed.entries:
                logger.warning(f"Feed parsing error for {feed_name}: {feed.bozo_exception}")
                return []

            for entry in feed.entries[:self.max_entries_per_feed]:
                url = entry.get("link", "")
                if not url:
                    continue

                results.append({
                    "title": entry.get("title", "").strip(),
                    "url": url,
                    "author": entry.get("author", entry.get("dc_creator")),
                    "published_date": self._parse_date(entry),
                    "content": self._extract_content(entry),
                    "source_name": feed_name,  # Preserve specific feed name
                    "metadata": {
                        "feed_name": feed_name,
                        "feed_url": feed_url,
                        "tags": [tag.get("term", "") for tag in entry.get("tags", [])],
                    },
                })

            logger.info(f"Fetched {len(results)} entries from {feed_name}")

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error fetching feed {feed_name}: {e}")
        except Exception as e:
            logger.error(f"Error parsing feed {feed_name}: {e}")

        return results

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch from all configured RSS feeds."""
        all_results = []

        for feed_name, feed_url in self.feeds.items():
            results = self._fetch_feed(feed_name, feed_url)
            all_results.extend(results)

        return all_results

    def fetch_feed(self, feed_name: str) -> List[Dict[str, Any]]:
        """Fetch from a specific feed by name."""
        if feed_name not in self.feeds:
            logger.warning(f"Unknown feed: {feed_name}")
            return []

        return self._fetch_feed(feed_name, self.feeds[feed_name])

    def add_feed(self, name: str, url: str):
        """Add a new feed to monitor."""
        self.feeds[name] = url
        logger.info(f"Added feed: {name}")

    def remove_feed(self, name: str):
        """Remove a feed from monitoring."""
        if name in self.feeds:
            del self.feeds[name]
            logger.info(f"Removed feed: {name}")
