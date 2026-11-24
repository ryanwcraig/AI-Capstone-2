"""Parsing agent for extracting deal information from coupon book images."""

import re
from pathlib import Path
from typing import List, Dict, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from costco_coupon_scraper import CouponDatabase

from ..react_loop import ReActAgent
from ..config import Config
from ..ocr import OCRService
from ..logger import get_logger


class Deal:
    """Represents a single extracted coupon deal."""

    def __init__(self, title: str, description: str = "", discount: Optional[str] = None,
                 url: Optional[str] = None, book_id: Optional[int] = None,
                 page_number: Optional[int] = None,
                 valid_from: Optional[str] = None, valid_to: Optional[str] = None):
        self.title = title
        self.description = description
        self.discount = discount
        self.url = url
        self.book_id = book_id
        self.page_number = page_number
        self.valid_from = valid_from
        self.valid_to = valid_to

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "discount": self.discount,
            "url": self.url,
            "book_id": self.book_id,
            "page_number": self.page_number,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }

    def text_for_classification(self) -> str:
        """Return combined text for classification purposes."""
        return f"{self.title}. {self.description}"


class ParsingAgent(ReActAgent):
    """Extracts deal information from coupon book images using OCR and segmentation."""

    # Discount patterns to detect — STRICT: must contain "OFF"
    DISCOUNT_PATTERNS = [
        r'\$\d+\.?\d*\s*(?:off|OFF)',   # $5 OFF, $5.50 off
        r'\d+%?\s*(?:off|OFF)',         # 25% OFF, 10 off
        r'(?:off|OFF)\s*\$[\d,]+\.?\d*', # OFF $5
    ]

    def __init__(self, config: Config = None):
        super().__init__("Parsing", config)
        self.config = self.config or Config()
        self.db = CouponDatabase(self.config.DB_PATH)
        self.ocr_service = OCRService(self.config.CACHE_DIR / "ocr")

    def _extract_discount(self, text: str) -> Optional[str]:
        """Extract discount information from deal text."""
        for pattern in self.DISCOUNT_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return match.group(0).strip()
        return None

    # Words/phrases that indicate boilerplate rather than a deal title
    NOISE_STARTS = [
        'valid', 'expires', 'limit', 'one per', 'code', 'offer', 'must',
        'terms', 'conditions', 'savings', 'pricing', 'price', 'shipping',
        'handling', 'charges', 'portion', 'active', 'membership', 'required',
        'unless', 'otherwise', 'noted', 'available', 'online', 'warehouse',
        'locations', 'higher', 'may', 'apply', 'products', 'additional',
        'alaska', 'hawaii', 'business', 'centers', 'include', 'will', 'can',
        'all', 'u.s.', 'costco.com', 'indicated', 'redemption', 'redeem',
        'presented', 'proof', 'receipt', 'void', 'where', 'prohibited',
        'restrictions', 'exclusions', 'apply', 'see', 'store', 'manager',
        'substitution', 'gift', 'card', 'cash', 'value', 'maximum', 'per',
        'household', 'cannot', 'combine', 'with', 'other', 'coupon', 'promotion',
        'save', 'savings', 'your cost', 'item ', 'limit ', 'checkout',
        'additional', 'may be', 'must be', 'while supplies', 'available',
    ]

    # Exact phrases that are definitely not deal titles (boilerplate)
    NOISE_EXACT = {
        '+online', '+business center', 'your cost', 'instant savings',
        'selection varies', 'no limit', 'save', 'limit 5', 'item limits',
        'one per household', 'while supplies last', 'additional state',
        'sales tax may be due', 'costcoinsider.com', 'terms and conditions',
    }

    # Patterns for noise titles (regex)
    NOISE_PATTERNS = [
        r'^\+online$',
        r'^\+business center$',
        r'^save \$?\d*\.?\d*$',
        r'^your cost.*$',
        r'^item \d{6,}.*$',
        r'^limit \d+$',
        r'^\$?\d+\.?\d*\s*(off|OFF)$',
    ]

    # Patterns that strongly indicate a real deal — STRICT: must contain "OFF"
    DEAL_SIGNALS = [
        r'\$\d+\.?\d*\s*OFF',           # $5 OFF, $5.50 OFF
        r'\d+%?\s*OFF',                 # 25% OFF, 10 OFF
        r'OFF\s*\$',                    # OFF $5 (reversed)
    ]

    def _extract_deals_from_text(self, text: str, book_id: int, page_number: int) -> List[Deal]:
        """Parse deals from OCR'd text using segmentation.

        Uses line-based segmentation with context windows to identify individual deals.
        A deal is confirmed when the title plus its context window contains a deal signal
        (price, discount, quantity indicator).
        """
        if not text:
            return []

        lines = [line.strip() for line in text.split('\n') if line.strip()]
        deals = []

        # Heuristic deal detection: look for lines that might be deal titles
        potential_starts = []
        for i, line in enumerate(lines):
            if self._is_potential_deal_title(line):
                potential_starts.append(i)

        # Extract deals with context windows (~100 tokens with overlap)
        context_size = 6  # Lines of context after title
        overlap = 2       # Overlap lines between contexts
        last_end = -1

        for start_idx in potential_starts:
            if start_idx < last_end and start_idx > 0:
                continue  # Skip if within previous deal's context

            end_idx = min(start_idx + context_size, len(lines))
            raw_title = lines[start_idx]
            description = " ".join(lines[start_idx + 1:end_idx])
            full_text = f"{raw_title}. {description}"

            # Require a deal signal in the context to confirm this is a real deal
            if not self._has_deal_signal(full_text):
                continue

            discount = self._extract_discount(full_text)

            # Clean up title: strip leading discount like "$4 OFF " or "25% OFF "
            title = re.sub(r'^\$?\s*\d+\.?\d*\s*%?\s*OFF\s+', '', raw_title, flags=re.IGNORECASE).strip()
            if not title:
                title = raw_title

            deal = Deal(
                title=title,
                description=description,
                discount=discount,
                book_id=book_id,
                page_number=page_number
            )
            deals.append(deal)
            last_end = end_idx - overlap

        return deals

    def _has_deal_signal(self, text: str) -> bool:
        """Check if text contains signals indicating a real coupon deal."""
        for pattern in self.DEAL_SIGNALS:
            if re.search(pattern, text):
                return True
        return False

    def _is_potential_deal_title(self, line: str) -> bool:
        """Heuristic to identify potential deal title lines."""
        normalized = line.strip().lower()

        # Exact matches for known noise phrases
        if normalized in self.NOISE_EXACT:
            return False

        # Regex patterns for noise titles
        for pattern in self.NOISE_PATTERNS:
            if re.match(pattern, normalized):
                return False

        # Must have some capital letters (product names)
        if not any(c.isupper() for c in line):
            return False
        # Shouldn't be too short or too long
        if len(line) < 4 or len(line) > 80:
            return False
        # Exclude lines starting with numbers (likely prices/percentages)
        if line[0].isdigit():
            return False
        # Exclude common non-title words
        if normalized.startswith(tuple(self.NOISE_STARTS)):
            return False
        # Exclude lines that are mostly punctuation or symbols
        letters = sum(c.isalpha() for c in line)
        if len(line) > 0 and letters / len(line) < 0.4:
            return False
        return True

    def parse_book(self, month: str, year: int) -> List[Deal]:
        """Parse all deals from a coupon book.

        Returns list of Deal objects extracted from the book's images.
        """
        book_id = self.db.find_book(year, month.lower())
        if book_id is None:
            self.logger.warning(f"Book not found in database: {month} {year}")
            return []

        # Fetch validity dates for this book to attach to deals
        book = self._get_book(book_id)
        valid_from = book['valid_from'] if book else None
        valid_to = book['valid_to'] if book else None

        pages = self.db.get_pages_for_book(book_id)
        all_deals = []

        for page in pages:
            image_path = Path(page['image_path'])
            if not image_path.exists():
                continue

            self.logger.info(f"Processing page {page['page_number']}...")
            text = self.ocr_service.extract_text(image_path)
            if text is None:
                continue

            deals = self._extract_deals_from_text(text, book_id, page['page_number'])
            all_deals.extend(deals)
            self.logger.info(f"  Extracted {len(deals)} potential deals from page {page['page_number']}")

        # Attach validity dates to all deals from this book
        for deal in all_deals:
            deal.valid_from = valid_from
            deal.valid_to = valid_to

        return all_deals

    def _get_book(self, book_id: int):
        """Fetch a single book record including validity dates."""
        with self.db._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM coupon_books WHERE id = ?", (book_id,)
            ).fetchone()

    def plan(self, goal: str, context: dict = None):
        from ..react_loop import Plan
        return Plan(goal=goal)

    def execute(self, action: str, **kwargs) -> any:
        if action == "parse":
            month = kwargs.get('month', 'november')
            year = kwargs.get('year', 2026)
            deals = self.parse_book(month, year)
            return {"deals": deals, "count": len(deals)}
        return {}

    def observe(self, result: any) -> str:
        if isinstance(result, dict) and 'count' in result:
            return f"Parsed {result['count']} deals"
        return str(result)

    def correct(self, assessment: str) -> Optional[str]:
        # If few deals extracted, could try different segmentation parameters
        if "0 deals" in assessment or "few deals" in assessment.lower():
            self.logger.info("Low deal extraction - considering parameter adjustment")
        return None
