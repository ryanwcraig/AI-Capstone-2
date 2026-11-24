#!/usr/bin/env python3
"""
Costco Coupon Book Scraper for costcoinsider.com

Scrapes all pages of a Costco coupon book from costcoinsider.com,
downloading each page's image to a local directory and tracking
downloads in a SQLite database.

URL structure:
    https://www.costcoinsider.com/costco-<month>-<year>-coupon-book/costco-<month>-<year>-coupon-book-page-<N>/

Usage:
    python3 costco_coupon_scraper.py
    python3 costco_coupon_scraper.py --month october --year 2026
    python3 costco_coupon_scraper.py --year-all 2025 --delay-min 1 --delay-max 3
    python3 costco_coupon_scraper.py --url https://www.costcoinsider.com/costco-october-2026-coupon-book/costco-october-2026-coupon-book-page-1/
"""

import argparse
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_BASE = PROJECT_ROOT / 'coupons'
DB_PATH = PROJECT_ROOT / 'coupons.db'

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}
BASE_DOMAIN = 'https://www.costcoinsider.com'
ALLOWED_DOMAINS = {'costcoinsider.com', 'www.costcoinsider.com'}
REQUEST_TIMEOUT = 30              # seconds per request
DELAY_MIN = 2.0                   # minimum delay between requests (seconds) - increased for rate-limit safety
DELAY_MAX = 3.0                   # maximum delay between requests (seconds)
MAX_RETRIES = 3                   # retry on transient failures
RETRY_BACKOFF = 2.0               # multiplier for exponential backoff

# Time scope constraint: only fetch coupon books from January 2026 onwards
MIN_START_DATE = datetime(2026, 1, 1)

MONTH_NAME_TO_NUM = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
class CouponDatabase:
    """SQLite database for tracking coupon book downloads."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS coupon_books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    total_pages INTEGER DEFAULT 0,
                    total_size_bytes INTEGER DEFAULT 0,
                    valid_from TEXT,
                    valid_to TEXT,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(year, month)
                );

                CREATE TABLE IF NOT EXISTS coupon_pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL,
                    page_number INTEGER NOT NULL,
                    image_url TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    file_size_bytes INTEGER DEFAULT 0,
                    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (book_id) REFERENCES coupon_books(id),
                    UNIQUE(book_id, page_number)
                );

                CREATE INDEX IF NOT EXISTS idx_pages_book ON coupon_pages(book_id);
                CREATE INDEX IF NOT EXISTS idx_pages_page ON coupon_pages(book_id, page_number);
            """)

    def find_book(self, year: int, month: str) -> Optional[int]:
        """Return book id if it exists, else None."""
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT id FROM coupon_books WHERE year = ? AND month = ?',
                (year, month.lower())
            ).fetchone()
            return row['id'] if row else None

    def create_book(self, year: int, month: str) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                'INSERT INTO coupon_books (year, month) VALUES (?, ?)',
                (year, month.lower())
            )
            return cur.lastrowid

    def update_book(self, book_id: int, total_pages: int, total_size_bytes: int) -> None:
        with self._get_conn() as conn:
            conn.execute(
                'UPDATE coupon_books SET total_pages = ?, total_size_bytes = ?, downloaded_at = CURRENT_TIMESTAMP WHERE id = ?',
                (total_pages, total_size_bytes, book_id)
            )

    def page_exists(self, book_id: int, page_number: int) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                'SELECT 1 FROM coupon_pages WHERE book_id = ? AND page_number = ?',
                (book_id, page_number)
            ).fetchone()
            return row is not None

    def record_page(
        self, book_id: int, page_number: int, image_url: str,
        image_path: str, file_size_bytes: int
    ) -> None:
        with self._get_conn() as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO coupon_pages
                   (book_id, page_number, image_url, image_path, file_size_bytes, downloaded_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                (book_id, page_number, image_url, image_path, file_size_bytes)
            )

    def get_all_books(self) -> list:
        with self._get_conn() as conn:
            return conn.execute(
                'SELECT * FROM coupon_books ORDER BY year DESC, month'
            ).fetchall()

    def get_pages_for_book(self, book_id: int) -> list:
        with self._get_conn() as conn:
            return conn.execute(
                'SELECT * FROM coupon_pages WHERE book_id = ? ORDER BY page_number',
                (book_id,)
            ).fetchall()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def build_page_url(month: str, year: int, page: int) -> str:
    """Construct a page URL from month, year, and page number."""
    month_lower = month.lower()
    slug = f"costco-{month_lower}-{year}-coupon-book"
    return f"{BASE_DOMAIN}/{slug}/{slug}-page-{page}/"


def random_delay(base: float = 0) -> float:
    """Return a random delay in seconds (optionally plus a *base* offset)."""
    return base + random.uniform(DELAY_MIN, DELAY_MAX)


def fetch_page(url: str) -> Optional[requests.Response]:
    """
    Fetch a URL with retries and exponential backoff.

    Returns the Response object or None if all retries fail.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            # 404 means the page doesn't exist — not a retryable error
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            wait = DELAY_MIN * (RETRY_BACKOFF ** (attempt - 1))
            print(f"  [retry {attempt}/{MAX_RETRIES}] {type(e).__name__}: {e}  "
                  f"— retrying in {wait:.1f}s", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(wait)
    return None


def download_image(url: str, dest_path: Path) -> bool:
    """Download an image from *url* and save it to *dest_path*."""
    resp = fetch_page(url)
    if resp is None or resp.status_code != 200:
        print(f"  [error] Could not download image: {url}", file=sys.stderr)
        return False
    with open(dest_path, 'wb') as f:
        f.write(resp.content)
    return True


# ---------------------------------------------------------------------------
# Core scraper
# ---------------------------------------------------------------------------
def scrape_coupon_book(
    start_url: str,
    output_dir: Path,
    download_images: bool = True,
    db: Optional[CouponDatabase] = None,
) -> dict:
    """
    Scrape every page of a Costco coupon book starting from *start_url*.

    The function follows the 'Next' pagination links until no more pages
    are found.  For each page it extracts the coupon image and (optionally)
    downloads it, recording results in the database.

    Parameters
    ----------
    start_url : str
        URL of page 1 of the coupon book.
    output_dir : Path
        Directory where images will be saved.
    download_images : bool
        If True (default), download each page's image.
    db : Optional[CouponDatabase]
        Database instance for tracking downloads.

    Returns
    -------
    dict
        Summary with keys: month, year, total_pages, total_size_bytes, pages.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    current_url = start_url
    page_num = 0
    total_size = 0
    pages = []

    # Parse year and month from URL for DB tracking
    # URL format: /costco-{month}-{year}-coupon-book/...page-{N}/
    year_match = re.search(r'costco-\w+-(\d{4})-coupon-book', start_url)
    month_match = re.search(r'costco-(\w+)-\d{4}-coupon-book', start_url)
    year = int(year_match.group(1)) if year_match else 0
    month = month_match.group(1).lower() if month_match else ''

    print(f"\n{'='*60}")
    print(f"  Costco Coupon Book Scraper")
    print(f"  Starting from: {start_url}")
    print(f"  Output directory: {output_dir}")
    print(f"  Random delay: {DELAY_MIN:.1f}s – {DELAY_MAX:.1f}s per request")
    print(f"{'='*60}\n")

    while current_url:
        page_num += 1

        # ── Fetch the page HTML
        resp = fetch_page(current_url)
        if resp is None:
            print(f"Page {page_num}: [error] Could not fetch {current_url} (giving up)")
            break

        # If we got a 404, stop — we've gone past the last page
        if resp.status_code == 404:
            print(f"Page {page_num}: [404] Not Found — stopping")
            break

        print(f"\n--- Page {page_num}: {current_url} ---")
        print(f"  Status: {resp.status_code}")

        soup = BeautifulSoup(resp.text, 'lxml')

        # ── Extract the coupon image
        image_url = find_coupon_image(soup)
        if image_url:
            image_url = urljoin(BASE_DOMAIN, image_url)
            print(f"  Image: {image_url}")

            # ── Download the image
            if download_images:
                filename = Path(urlparse(image_url).path).name
                dest = output_dir / filename
                if download_image(image_url, dest):
                    file_size = dest.stat().st_size
                    total_size += file_size
                    print(f"  [saved] {dest} ({file_size / 1024:.1f} KB)")

                    # Record in database
                    if db:
                        book_id = db.find_book(year, month)
                        if book_id is None:
                            book_id = db.create_book(year, month)
                        if not db.page_exists(book_id, page_num):
                            db.record_page(
                                book_id, page_num, image_url,
                                str(dest), file_size
                            )

                    pages.append({
                        'page': page_num,
                        'image_url': image_url,
                        'image_path': str(dest),
                        'size': file_size,
                    })
                else:
                    print(f"  [error] Failed to save image for page {page_num}")
        else:
            print("  [warning] No coupon image found on this page!")

        # ── Find the next page
        next_url = find_next_page(soup)
        if next_url:
            print(f"  Next: {next_url}")
            current_url = next_url
        else:
            print("  No 'Next' link — reached the last page.")
            current_url = None

        # ── Be polite with randomized delay to avoid scraper detection
        if current_url:
            delay = random_delay()
            time.sleep(delay)

    # ── Update book record in database
    if db and year and month:
        book_id = db.find_book(year, month)
        if book_id is None:
            book_id = db.create_book(year, month)
        db.update_book(book_id, page_num, total_size)

    # ── Summary
    print(f"\n{'='*60}")
    print(f"  DONE — scraped {page_num} page(s)")
    file_count = len(list(output_dir.glob('*.jpg'))) + len(list(output_dir.glob('*.jpeg')))
    print(f"  Images in directory: {file_count}")
    print(f"  Total size: {total_size / (1024*1024):.1f} MB")
    print(f"{'='*60}\n")

    return {
        'month': month,
        'year': year,
        'total_pages': page_num,
        'total_size_bytes': total_size,
        'pages': pages,
    }


def find_coupon_image(soup: BeautifulSoup) -> Optional[str]:
    """
    Find the main coupon-book page image in the parsed HTML.
    """
    for img in soup.find_all('img'):
        src = img.get('src', '')
        alt = img.get('alt', '')
        if 'wp-content/uploads' in src and 'Coupon-Book' in src:
            return src
        if 'wp-content/uploads' in src and 'coupon' in alt.lower():
            return src
    return None


def find_next_page(soup: BeautifulSoup) -> Optional[str]:
    """Find the 'Next' pagination link and return its absolute URL."""
    for a in soup.find_all('a'):
        text = a.get_text(strip=True)
        if 'next' in text.lower():
            href = a.get('href', '')
            if href:
                return urljoin(BASE_DOMAIN, href)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
ALL_MONTHS = [
    'january', 'february', 'march', 'april', 'may', 'june',
    'july', 'august', 'september', 'october', 'november', 'december'
]


def main():
    global DELAY_MIN, DELAY_MAX

    parser = argparse.ArgumentParser(
        description="Scrape all pages of a Costco coupon book from costcoinsider.com"
    )
    parser.add_argument(
        '--month', default='october',
        help='Month of the coupon book (e.g. october). Default: october'
    )
    parser.add_argument(
        '--year', type=int, default=2026,
        help='Year of the coupon book (e.g. 2026). Default: 2026'
    )
    parser.add_argument(
        '--url', type=str, default=None,
        help='Full URL of page 1 (overrides --month/--year)'
    )
    parser.add_argument(
        '--output', '-o', type=str, default=None,
        help='Output directory for images. Default: <project>/coupons/<year>/<month>/'
    )
    parser.add_argument(
        '--no-download', action='store_true',
        help='Only list image URLs without downloading'
    )
    parser.add_argument(
        '--year-all', type=int, default=None,
        help='Download all 12 monthly coupon books for the given year (e.g. 2025)'
    )
    parser.add_argument(
        '--delay-min', type=float, default=DELAY_MIN,
        help=f'Minimum delay between requests in seconds. Default: {DELAY_MIN}'
    )
    parser.add_argument(
        '--delay-max', type=float, default=DELAY_MAX,
        help=f'Maximum delay between requests in seconds. Default: {DELAY_MAX}'
    )
    parser.add_argument(
        '--import-existing', action='store_true',
        help='Scan existing coupons/ directory and import all downloaded images into the database'
    )
    parser.add_argument(
        '--db-info', action='store_true',
        help='Show database summary of all downloaded coupon books and exit'
    )
    args = parser.parse_args()

    DELAY_MIN = args.delay_min
    DELAY_MAX = args.delay_max
    download = not args.no_download

    # Initialize database
    db = CouponDatabase(DB_PATH)

    # ── Import existing files from coupons/ directory into database
    if args.import_existing:
        print(f"\n{'#'*60}")
        print(f"  IMPORT EXISTING: Scanning {OUTPUT_BASE}")
        print(f"{'#'*60}")

        total_books = 0
        total_pages = 0

        for year_dir in sorted(OUTPUT_BASE.iterdir()):
            if not year_dir.is_dir():
                continue
            year = int(year_dir.name)
            for month_dir in sorted(year_dir.iterdir()):
                if not month_dir.is_dir():
                    continue
                month = month_dir.name.lower()
                images = sorted(
                    [f for f in month_dir.iterdir() if f.suffix.lower() in ('.jpg', '.jpeg', '.png')]
                )
                if not images:
                    print(f"  Skipping {month.capitalize()} {year}: no images found")
                    continue

                print(f"\n  Importing {month.capitalize()} {year}: {len(images)} images")
                book_id = db.find_book(year, month)
                if book_id is None:
                    book_id = db.create_book(year, month)

                total_size = 0
                for img_path in images:
                    # Extract page number from filename like "Costco-October-2026-Coupon-Book-Page-1.jpg"
                    page_match = re.search(r'Page-(\d+)', img_path.name)
                    page_num = int(page_match.group(1)) if page_match else 0

                    if not db.page_exists(book_id, page_num):
                        file_size = img_path.stat().st_size
                        total_size += file_size
                        # Reconstruct image URL from the filename
                        image_url = f"{BASE_DOMAIN}/wp-content/uploads/{year:04d}/{month}/{img_path.name}"
                        db.record_page(book_id, page_num, image_url, str(img_path), file_size)
                    total_pages += 1

                db.update_book(book_id, len(images), total_size)
                total_books += 1

        print(f"\n{'#'*60}")
        print(f"  IMPORT COMPLETE: {total_books} books, {total_pages} pages")
        print(f"{'#'*60}\n")
        return

    # ── DB info mode
    if args.db_info:
        books = db.get_all_books()
        print(f"\n{'='*70}")
        print(f"  Coupon Download Database: {DB_PATH}")
        print(f"{'='*70}")
        if not books:
            print("  No books in database.")
        else:
            for book in books:
                pages = db.get_pages_for_book(book['id'])
                print(f"\n  {book['month'].capitalize()} {book['year']}: "
                      f"{book['total_pages']} pages, "
                      f"{book['total_size_bytes'] / (1024*1024):.1f} MB, "
                      f"downloaded {book['downloaded_at']}")
                for pg in pages:
                    print(f"    Page {pg['page_number']}: {Path(pg['image_path']).name} "
                          f"({pg['file_size_bytes'] / 1024:.1f} KB)")
        print(f"\n{'='*70}\n")
        return

    # ── Batch mode: all 12 months of a year
    if args.year_all is not None:
        year = args.year_all
        print(f"\n{'#'*60}")
        print(f"  BATCH MODE: Downloading all coupon books for {year}")
        print(f"  Random delay: {DELAY_MIN:.1f}s – {DELAY_MAX:.1f}s per request")
        print(f"{'#'*60}")

        for month in ALL_MONTHS:
            start_url = build_page_url(month, year, 1)
            output_dir = OUTPUT_BASE / str(year) / month

            print(f"\n>>> Processing: {month.capitalize()} {year}")
            scrape_coupon_book(start_url, output_dir, download_images=download, db=db)

            # Random delay between months
            if download:
                delay = random_delay(base=1.0)
                print(f"  Sleeping {delay:.1f}s before next month...\n")
                time.sleep(delay)

        print(f"\n{'#'*60}")
        print(f"  BATCH COMPLETE — all books for {year} processed")
        print(f"{'#'*60}\n")
        return

    # ── Single-book mode
    if args.url:
        start_url = args.url
    else:
        start_url = build_page_url(args.month, args.year, 1)

    month_lower = args.month.lower()
    default_output = OUTPUT_BASE / str(args.year) / month_lower

    if args.output:
        output_dir = Path(args.output)
    else:
        output_dir = default_output

    scrape_coupon_book(start_url, output_dir, download_images=download, db=db)


if __name__ == '__main__':
    main()
