"""Base scraper class with common functionality."""

import time
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

from ..config import REQUEST_DELAY

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    def __init__(self, source_name: str, source_type: str):
        self.source_name = source_name
        self.source_type = source_type
        self.last_request_time = 0

    def rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self.last_request_time
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()

    @abstractmethod
    def fetch(self) -> List[Dict[str, Any]]:
        """
        Fetch content from the source.

        Returns:
            List of dictionaries containing:
                - title: str
                - url: str
                - author: Optional[str]
                - published_date: Optional[datetime]
                - content: Optional[str]
                - metadata: Optional[Dict]
        """
        pass

    def process_results(self, results: List[Dict]) -> List[Dict]:
        """
        Post-process fetched results.
        Override in subclasses for source-specific processing.
        """
        processed = []
        for item in results:
            # Ensure required fields
            if not item.get("title") or not item.get("url"):
                continue

            # Normalize the item - preserve source_name from item if it exists
            processed_item = {
                "title": item["title"].strip(),
                "url": item["url"].strip(),
                "author": item.get("author"),
                "published_date": item.get("published_date"),
                "content": item.get("content", ""),
                "metadata": item.get("metadata", {}),
                "source_name": item.get("source_name") or self.source_name,
                "source_type": self.source_type,
            }
            processed.append(processed_item)

        return processed

    def run(self) -> List[Dict[str, Any]]:
        """Execute the scraper and return processed results."""
        logger.info(f"Starting scrape for {self.source_name}")
        try:
            raw_results = self.fetch()
            processed = self.process_results(raw_results)
            logger.info(
                f"Scraped {len(processed)} items from {self.source_name}"
            )
            return processed
        except Exception as e:
            logger.error(f"Error scraping {self.source_name}: {e}")
            return []
