#!/usr/bin/env python3
"""Add valid_from and valid_to fields to the database and CSV.

Extracts validity dates from each coupon book's page 1 using OCR,
then updates the deals table with the respective book's dates.
"""

import re
import sqlite3
from datetime import datetime, date
from pathlib import Path

from agent.config import Config
from agent.ocr import OCRService

MONTHS = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
    'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
}


def month_to_num(month_name: str) -> int | None:
    """Convert month name or abbreviation to number."""
    month_lower = month_name.lower().rstrip('.')
    for name, num in MONTHS.items():
        if name.startswith(month_lower):
            return num
    return None


def extract_validity_dates(text: str, fallback_year: int) -> tuple[str, str] | None:
    """Extract validity start and end dates from coupon book text.

    Looks for patterns like:
        "Savings valid Oct. 19 - Nov. 15, 2026"
        "Valid from September 21 through October 18, 2025"
        "Sept 21 - Oct 18, 2025"

    Returns (valid_from, valid_to) as ISO date strings, or None if not found.
    """
    if not text:
        return None

    # Pattern to match a single month-day[-year] date (with optional period after month)
    DATE = r'([A-Za-z]+)\.?\s+(\d{1,2})\s*,?\s*(\d{4})?'

    # Use re.DOTALL so \s can match newlines between "valid" and dates
    flags = re.DOTALL

    # Pattern 0: Same month range like "March 5 - 30, 2025" (end date has no month)
    same_month_pattern = r'[Vv]alid\s*(?:from)?\s*([A-Za-z]+)\.?\s+(\d{1,2})\s*,?\s*(\d{4})?\s*[-–—]\s*(\d{1,2})\s*,?\s*(\d{4})?'
    match = re.search(same_month_pattern, text, flags)
    if match:
        try:
            month_name, start_day, year_str = match.group(1), int(match.group(2)), match.group(3) or fallback_year
            end_day = int(match.group(4))
            month_num = month_to_num(month_name)
            if month_num:
                valid_from = date(int(year_str), month_num, start_day)
                valid_to = date(int(year_str), month_num, end_day)
                if valid_from <= valid_to:
                    return (valid_from.isoformat(), valid_to.isoformat())
        except Exception:
            pass

    # Pattern 1: "Savings valid Oct. 19 - Nov. 15, 2026" or similar
    pattern1 = rf'[Vv]alid\s*(?:from)?\s*{DATE}\s*[-–—]\s*{DATE}'
    match = re.search(pattern1, text, flags)

    # Pattern 2: "Valid from September 21 through October 18, 2025"
    if not match:
        pattern2 = rf'[Vv]alid\s*(?:from)?\s*{DATE}\s*,?\s*(?:through|to|-)\s*{DATE}'
        match = re.search(pattern2, text, flags)

    # Pattern 3: Standalone date ranges like "Sept 21 - Oct 18, 2025"
    if not match:
        pattern3 = rf'{DATE}\s*[-–—]\s*{DATE}'
        match = re.search(pattern3, text, flags)

    if not match:
        return None

    # Parse the matched groups
    try:
        start_month_name, start_day, start_year_str = match.group(1), int(match.group(2)), match.group(3)
        end_month_name, end_day, end_year_str = match.group(4), int(match.group(5)), match.group(6)

        start_month_num = month_to_num(start_month_name)
        end_month_num = month_to_num(end_month_name)

        if not start_month_num or not end_month_num:
            return None

        # Determine years
        start_year = int(start_year_str) if start_year_str else fallback_year
        end_year = int(end_year_str) if end_year_str else None

        # If end year not explicitly stated and range spans a year (e.g., Dec to Jan), adjust
        if end_year is None:
            end_year = start_year
            if end_month_num < start_month_num:
                end_year += 1

        valid_from = date(start_year, start_month_num, start_day)
        valid_to = date(end_year, end_month_num, end_day)

        # Sanity check: from should be before to
        if valid_from > valid_to:
            return None

        return (valid_from.isoformat(), valid_to.isoformat())

    except Exception:
        return None


def main():
    config = Config()
    ocr_service = OCRService(config.CACHE_DIR / "ocr")

    # Get all books from database
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    books = conn.execute("SELECT * FROM coupon_books ORDER BY year, month").fetchall()

    print(f"Processing {len(books)} coupon books...")
    print("=" * 60)

    for book in books:
        month = book['month']
        year = book['year']
        book_id = book['id']

        # Find page 1 image path
        pages = conn.execute(
            "SELECT image_path FROM coupon_pages WHERE book_id = ? ORDER BY page_number LIMIT 1",
            (book_id,)
        ).fetchone()

        if not pages:
            print(f"  [{month.capitalize()} {year}] No pages found")
            continue

        image_path = Path(pages['image_path'])
        if not image_path.exists():
            print(f"  [{month.capitalize()} {year}] Image not found: {image_path}")
            continue

        # Extract text via OCR (uses cache)
        print(f"  [{month.capitalize()} {year}] Processing page 1...", end=" ", flush=True)
        text = ocr_service.extract_text(image_path)

        if text is None:
            print("OCR failed")
            continue

        # Extract validity dates using book's year as fallback
        dates = extract_validity_dates(text, year)
        if dates:
            valid_from, valid_to = dates
            conn.execute(
                "UPDATE coupon_books SET valid_from = ?, valid_to = ? WHERE id = ?",
                (valid_from, valid_to, book_id)
            )
            print(f"  {valid_from} through {valid_to}")
        else:
            # Try page 2 as fallback
            pages2 = conn.execute(
                "SELECT image_path FROM coupon_pages WHERE book_id = ? ORDER BY page_number LIMIT 1 OFFSET 1",
                (book_id,)
            ).fetchone()

            if pages2:
                image_path2 = Path(pages2['image_path'])
                if image_path2.exists():
                    print("Trying page 2...", end=" ", flush=True)
                    text2 = ocr_service.extract_text(image_path2)
                    dates = extract_validity_dates(text2, year) if text2 else None
                    if dates:
                        valid_from, valid_to = dates
                        conn.execute(
                            "UPDATE coupon_books SET valid_from = ?, valid_to = ? WHERE id = ?",
                            (valid_from, valid_to, book_id)
                        )
                        print(f"  {valid_from} through {valid_to}")
                    else:
                        print(" Could not find validity dates")
                else:
                    print(" No page 2 found")
            else:
                print(" Could not find validity dates")

    conn.commit()
    conn.close()

    # Update deals table with validity dates from parent book
    print("\nUpdating deals table with validity dates...")
    conn = sqlite3.connect(config.DB_PATH)
    result = conn.execute("""
        UPDATE deals SET valid_from = (SELECT valid_from FROM coupon_books WHERE id = deals.book_id),
                         valid_to = (SELECT valid_to FROM coupon_books WHERE id = deals.book_id)
    """)
    print(f"  Updated {result.rowcount} deals")

    # Verify
    conn.commit()
    print("\nSummary:")
    books_with_dates = conn.execute(
        "SELECT month, year, valid_from, valid_to FROM coupon_books WHERE valid_from IS NOT NULL ORDER BY year, month"
    ).fetchall()
    print(f"  {len(books_with_dates)} books with validity dates")
    for book in books_with_dates:
        print(f"    {book[0].capitalize()} {book[1]}: {book[2]} through {book[3]}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
