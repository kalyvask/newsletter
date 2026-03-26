"""Reddit scraper using PRAW library or JSON fallback."""

import logging
import requests
from datetime import datetime
from typing import List, Dict, Any, Optional
import time

try:
    import praw
    PRAW_AVAILABLE = True
except ImportError:
    PRAW_AVAILABLE = False

from .base import BaseScraper
from ..config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_USER_AGENT,
    REDDIT_SUBREDDITS,
    REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)


class RedditScraper(BaseScraper):
    """Scraper for Reddit posts and comments. Uses API if available, otherwise JSON endpoints."""

    def __init__(
        self,
        subreddits: Optional[List[str]] = None,
        posts_per_subreddit: int = 25,
        time_filter: str = "week",
    ):
        super().__init__("Reddit", "reddit")
        self.subreddits = subreddits or REDDIT_SUBREDDITS
        self.posts_per_subreddit = posts_per_subreddit
        self.time_filter = time_filter
        self.reddit = None
        self.use_json_fallback = True  # Always try JSON fallback if API fails

        # Try to initialize PRAW if credentials available
        if PRAW_AVAILABLE and REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET:
            try:
                self.reddit = praw.Reddit(
                    client_id=REDDIT_CLIENT_ID,
                    client_secret=REDDIT_CLIENT_SECRET,
                    user_agent=REDDIT_USER_AGENT,
                )
                logger.info("Reddit API initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize Reddit API: {e}")
        else:
            logger.info("Reddit API not configured - using JSON endpoint fallback")

    def _fetch_via_json(self, subreddit_name: str, sort: str = "hot") -> List[Dict[str, Any]]:
        """Fetch posts using Reddit's public JSON endpoints (no API key required)."""
        results = []

        # Reddit provides JSON by appending .json to URLs
        url = f"https://www.reddit.com/r/{subreddit_name}/{sort}.json"
        params = {"limit": self.posts_per_subreddit}

        if sort == "top":
            params["t"] = self.time_filter  # day, week, month, year, all

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        try:
            self.rate_limit()
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)

            if response.status_code == 429:
                logger.warning(f"Rate limited on r/{subreddit_name}, waiting 10s and retrying...")
                time.sleep(10)
                # Retry once
                response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
                if response.status_code == 429:
                    logger.warning(f"Still rate limited on r/{subreddit_name}, skipping")
                    return []

            response.raise_for_status()
            data = response.json()

            posts = data.get("data", {}).get("children", [])

            for post_wrapper in posts:
                post = post_wrapper.get("data", {})

                if not post.get("title"):
                    continue

                permalink = post.get("permalink", "")
                post_url = f"https://reddit.com{permalink}" if permalink else ""

                results.append({
                    "title": post.get("title", ""),
                    "url": post_url,
                    "author": post.get("author"),
                    "published_date": datetime.utcfromtimestamp(post.get("created_utc", 0)) if post.get("created_utc") else None,
                    "content": (post.get("selftext") or "")[:2000],
                    "source_name": f"r/{subreddit_name}",
                    "metadata": {
                        "subreddit": subreddit_name,
                        "score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "upvote_ratio": post.get("upvote_ratio", 0),
                        "link_url": post.get("url") if not post.get("is_self") else None,
                        "flair": post.get("link_flair_text"),
                    },
                })

            logger.info(f"Fetched {len(results)} posts from r/{subreddit_name} via JSON")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching r/{subreddit_name} via JSON: {e}")
        except Exception as e:
            logger.error(f"Error parsing r/{subreddit_name} JSON: {e}")

        return results

    def fetch(self) -> List[Dict[str, Any]]:
        """Fetch posts from configured subreddits. Uses API if available, otherwise JSON fallback."""
        results = []
        seen_urls = set()

        # Try PRAW API first if available
        if self.reddit:
            for subreddit_name in self.subreddits:
                try:
                    self.rate_limit()
                    subreddit = self.reddit.subreddit(subreddit_name)

                    # Get hot and top posts
                    posts = list(subreddit.hot(limit=self.posts_per_subreddit))
                    posts.extend(
                        subreddit.top(
                            time_filter=self.time_filter,
                            limit=self.posts_per_subreddit
                        )
                    )

                    for post in posts:
                        post_url = f"https://reddit.com{post.permalink}"
                        if post_url in seen_urls:
                            continue
                        seen_urls.add(post_url)

                        # Get top comments for context
                        post.comments.replace_more(limit=0)
                        top_comments = []
                        for comment in post.comments[:5]:
                            if hasattr(comment, "body"):
                                top_comments.append(comment.body[:500])

                        results.append({
                            "title": post.title,
                            "url": post_url,
                            "author": str(post.author) if post.author else None,
                            "published_date": datetime.utcfromtimestamp(post.created_utc),
                            "content": post.selftext[:2000] if post.selftext else "",
                            "source_name": f"r/{subreddit_name}",
                            "metadata": {
                                "subreddit": subreddit_name,
                                "score": post.score,
                                "num_comments": post.num_comments,
                                "upvote_ratio": post.upvote_ratio,
                                "top_comments": top_comments,
                                "link_url": post.url if not post.is_self else None,
                            },
                        })

                    logger.info(f"Fetched posts from r/{subreddit_name} via API")

                except Exception as e:
                    logger.error(f"Error fetching from r/{subreddit_name} via API: {e}")
                    continue

            if results:
                return results

        # Fallback to JSON endpoints (no API key required)
        logger.info("Using Reddit JSON fallback (no API key)")

        for subreddit_name in self.subreddits:
            # Add delay between subreddits to avoid rate limiting
            if results:
                time.sleep(3)  # Increased delay

            # Fetch hot posts
            hot_posts = self._fetch_via_json(subreddit_name, "hot")
            for post in hot_posts:
                if post["url"] not in seen_urls:
                    seen_urls.add(post["url"])
                    results.append(post)

            # Longer delay before top posts
            time.sleep(2)

            # Fetch top posts
            top_posts = self._fetch_via_json(subreddit_name, "top")
            for post in top_posts:
                if post["url"] not in seen_urls:
                    seen_urls.add(post["url"])
                    results.append(post)

            # Log progress
            if len(results) % 50 == 0 and results:
                logger.info(f"Fetched {len(results)} posts so far...")

        return results

    def fetch_subreddit(self, subreddit_name: str) -> List[Dict[str, Any]]:
        """Fetch posts from a specific subreddit."""
        original_subs = self.subreddits
        self.subreddits = [subreddit_name]
        results = self.fetch()
        self.subreddits = original_subs
        return results
