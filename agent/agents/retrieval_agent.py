"""Retrieval agent for fetching Costco coupon books with rate-limit protections."""

import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from urllib.parse import urlparse

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from costco_coupon_scraper import (
    BASE_DOMAIN,
    CouponDatabase,
    build_page_url,
    fetch_page,
    download_image,
    scrape_coupon_book,
    random_delay,
    MONTH_NAME_TO_NUM,
)
from bs4 import BeautifulSoup

from ..react_loop import ReActAgent
from ..config import Config
from ..logger import get_logger


class RetrievalAgent(ReActAgent):
    """Handles fetching coupon books with rate-limiting, caching, and date filtering."""

    def __init__(self, config: Config = None):
        super().__init__("Retrieval", config)
        self.config = self.config or Config()
        self.db = CouponDatabase(self.config.DB_PATH)
        self.session_cache = {}  # In-session cache for fetched content
        self.cache_dir = self.config.CACHE_DIR / "html"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _check_domain_allowed(self, url: str) -> bool:
        """Enforce domain allowlist to prevent broad crawling."""
        parsed = urlparse(url)
        return parsed.netloc in self.config.ALLOWED_DOMAINS or \
               parsed.netloc.endswith(".costcoinsider.com")

    def _parse_validity_date(self, soup: BeautifulSoup) -> Optional[datetime]:
        """Extract validity start date from page content."""
        # Look for text patterns like "Valid from September 21" or dates in titles
        import re
        text = soup.get_text()
        # Common patterns: "Sept 21 - Oct 18", "September 21, 2025 through October 18, 2025"
        patterns = [
            r'[Vv]alid(?:ity)?\s+(?:from\s+)?([A-Za-z]+)\s+(\d{1,2})',
            r'([A-Za-z]+)\s+(\d{1,2}),?\s*(\d{4})\s*(?:-|through|to)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    month_name = match.group(1)
                    day = int(match.group(2))
                    year = int(match.group(3)) if match.lastindex >= 3 and match.group(3) else datetime.now().year
                    return datetime(year, MONTH_NAME_TO_NUM[month_name.lower()], day)
                except (ValueError, KeyError):
                    continue
        return None

    def _should_fetch_by_date(self, month: str, year: int) -> bool:
        """Check if a coupon book falls within the time scope constraint."""
        try:
            start_date = datetime(year, MONTH_NAME_TO_NUM[month.lower()], 1)
            return start_date >= self.config.MIN_START_DATE
        except KeyError:
            return False

    def _check_cache(self, url: str) -> Optional[str]:
        """Check if content is already cached (disk and session)."""
        # Check session cache first
        if url in self.session_cache:
            self.logger.debug(f"Session cache hit for {url}")
            return self.session_cache[url]

        # Check disk cache
        import hashlib
        cache_key = hashlib.md5(url.encode()).hexdigest()[:16]
        cache_path = self.cache_dir / f"{cache_key}.html"
        if cache_path.exists():
            try:
                content = cache_path.read_text(encoding="utf-8")
                self.session_cache[url] = content
                self.logger.debug(f"Disk cache hit for {url}")
                return content
            except Exception as e:
                self.logger.warning(f"Cache read error: {e}")

        return None

    def _save_to_cache(self, url: str, content: str):
        """Save fetched content to session and disk cache."""
        self.session_cache[url] = content

        import hashlib
        cache_key = hashlib.md5(url.encode()).hexdigest()[:16]
        cache_path = self.cache_dir / f"{cache_key}.html"
        try:
            cache_path.write_text(content, encoding="utf-8")
        except Exception as e:
            self.logger.warning(f"Cache write error: {e}")

    def _book_already_downloaded(self, month: str, year: int) -> bool:
        """Check if this coupon book has already been downloaded."""
        return self.db.find_book(year, month.lower()) is not None

    def _has_local_images(self, month: str, year: int) -> bool:
        """Check if images for this book already exist on disk (secondary cache)."""
        output_dir = self.config.OUTPUT_BASE / str(year) / month.lower()
        if not output_dir.exists():
            return False
        images = list(output_dir.glob("*.jpg")) + list(output_dir.glob("*.jpeg"))
        return len(images) > 0

    def _import_existing_images(self, month: str, year: int) -> dict:
        """Import existing local images into the database without fetching.

        Scans the local directory for already-downloaded images and records them
        in the database so parsing can proceed from cache.
        """
        import re as _re
        output_dir = self.config.OUTPUT_BASE / str(year) / month.lower()
        images = [f for f in output_dir.iterdir() if f.suffix.lower() in ('.jpg', '.jpeg', '.png')]

        # Natural sort by page number extracted from filename
        def _page_number(f):
            m = _re.search(r'Page-(\d+)', f.name)
            return int(m.group(1)) if m else None

        images.sort(key=lambda f: (_page_number(f) is None, _page_number(f) or 0))

        book_id = self.db.find_book(year, month.lower())
        if book_id is None:
            book_id = self.db.create_book(year, month.lower())

        total_size = 0
        for idx, img_path in enumerate(images, start=1):
            page_num = _page_number(img_path) or idx
            if not self.db.page_exists(book_id, page_num):
                file_size = img_path.stat().st_size
                total_size += file_size
                image_url = f"{BASE_DOMAIN}/wp-content/uploads/{year}/{month.lower()}/{img_path.name}"
                self.db.record_page(book_id, page_num, image_url, str(img_path), file_size)

        self.db.update_book(book_id, len(images), total_size)
        return {"total_pages": len(images)}

    def fetch_coupon_book(self, month: str, year: int, force: bool = False) -> dict:
        """Fetch a coupon book with all rate-limit protections.

        Returns summary dict with 'fetched' status and page information.
        """
        # Check time scope constraint
        if not self._should_fetch_by_date(month, year):
            self.logger.info(f"Skipping {month} {year}: outside time scope (min {self.config.MIN_START_DATE.date()})")
            return {"fetched": False, "reason": "outside_time_scope"}

        # Check secondary cache: if images exist on disk but not in DB, import them
        if self._has_local_images(month, year):
            self.logger.info(f"Found local images for {month} {year}, importing from cache")
            result = self._import_existing_images(month, year)
            return {"fetched": True, "from_cache": True, **result}

        # Check cache first before fetching
        if not force and self._book_already_downloaded(month, year):
            self.logger.info(f"Skipping {month} {year}: already downloaded (use force=True to override)")
            return {"fetched": False, "reason": "already_downloaded"}

        start_url = build_page_url(month, year, 1)

        # Enforce domain allowlist
        if not self._check_domain_allowed(start_url):
            self.logger.error(f"Domain not allowed: {start_url}")
            return {"fetched": False, "reason": "domain_not_allowed"}

        output_dir = self.config.OUTPUT_BASE / str(year) / month.lower()
        result = scrape_coupon_book(
            start_url,
            output_dir,
            download_images=True,
            db=self.db
        )

        return {"fetched": True, **result}

    def check_next_fetch_time(self) -> Optional[datetime]:
        """Determine when the next fetch should occur based on current validity window."""
        # Get most recently downloaded book
        books = self.db.get_all_books()
        if not books:
            return datetime.now()  # No books yet, can fetch now

        latest_book = books[0]  # Sorted by year DESC

        # Estimate next fetch: after the validity window expires
        # Typically books last ~30 days from start
        try:
            start_date = datetime(latest_book['year'], MONTH_NAME_TO_NUM[latest_book['month']], 1)
            # Add 35 days to be safe (covers typical monthly cycle + buffer)
            next_fetch = start_date.replace(day=1)
            if latest_book['month'] == 'december':
                from dateutil.relativedelta import relativedelta
                next_fetch = start_date + relativedelta(months=1)
            else:
                next_month = MONTH_NAME_TO_NUM[latest_book['month']] + 1
                if next_month > 12:
                    next_month = 1
                    year = latest_book['year'] + 1
                else:
                    year = latest_book['year']
                next_fetch = datetime(year, next_month, 1)

            return next_fetch
        except Exception as e:
            self.logger.warning(f"Could not calculate next fetch time: {e}")
            return None

    def plan(self, goal: str, context: dict = None):
        from ..react_loop import Plan
        return Plan(goal=goal)

    def execute(self, action: str, **kwargs) -> any:
        if action == "fetch":
            month = kwargs.get('month', 'november')
            year = kwargs.get('year', 2026)
            force = kwargs.get('force', False)
            return self.fetch_coupon_book(month, year, force)
        elif action == "check_fetch_time":
            return {"next_fetch_time": self.check_next_fetch_time()}
        return {}

    def observe(self, result: any) -> str:
        if isinstance(result, dict):
            if 'fetched' in result and result['fetched']:
                return f"Successfully fetched {result.get('total_pages', 0)} pages"
            elif 'reason' in result:
                return f"Not fetched: {result['reason']}"
            elif 'next_fetch_time' in result:
                return f"Next fetch time: {result['next_fetch_time']}"
        return str(result)

    def correct(self, assessment: str) -> Optional[str]:
        # If fetch failed due to network issues, could retry with longer delay
        if "error" in assessment.lower():
            self.logger.info("Assessing correction strategy...")
        return None
