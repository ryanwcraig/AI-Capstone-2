"""Structuring agent for classification, database writing, and scheduling."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from costco_coupon_scraper import CouponDatabase, MONTH_NAME_TO_NUM

from ..react_loop import ReActAgent
from ..config import Config
from ..classifier import DealClassifier
from ..agents.parsing_agent import Deal
from ..logger import get_logger


class StructuringAgent(ReActAgent):
    """Classifies deals, writes to database/spreadsheet, and schedules next runs."""

    def __init__(self, config: Config = None):
        super().__init__("Structuring", config)
        self.config = self.config or Config()
        self.db = CouponDatabase(self.config.DB_PATH)
        self.classifier = DealClassifier(self.config)
        self.schedule_state_path = self.config.PROJECT_ROOT / "schedule_state.json"
        self._init_deals_schema()

    def _init_deals_schema(self):
        """Create deals table if it doesn't exist."""
        with sqlite3.connect(self.config.DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS deals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL,
                    page_number INTEGER,
                    title TEXT NOT NULL,
                    description TEXT,
                    discount TEXT,
                    department TEXT DEFAULT 'General',
                    classified_by_llm BOOLEAN DEFAULT 0,
                    valid_from TEXT,
                    valid_to TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (book_id) REFERENCES coupon_books(id),
                    UNIQUE(book_id, page_number, title)
                );
                CREATE INDEX IF NOT EXISTS idx_deals_book ON deals(book_id);
                CREATE INDEX IF NOT EXISTS idx_deals_department ON deals(department);

                CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    next_fetch_after TIMESTAMP,
                    status TEXT DEFAULT 'completed'
                );
            """)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.config.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def classify_deals(self, deals: List[Deal]) -> int:
        """Classify all deals and return count that used LLM fallback."""
        llm_count = 0
        for deal in deals:
            department, used_llm = self.classifier.classify(deal.text_for_classification())
            deal.department = department
            deal.classified_by_llm = used_llm
            if used_llm:
                llm_count += 1

        return llm_count

    def write_deals_to_db(self, deals: List[Deal]) -> int:
        """Write classified deals to the database. Returns count inserted."""
        inserted = 0
        with self._get_conn() as conn:
            for deal in deals:
                try:
                    params = (
                        deal.book_id,
                        deal.page_number,
                        deal.title,
                        deal.description,
                        deal.discount,
                        getattr(deal, 'department', 'General'),
                        int(getattr(deal, 'classified_by_llm', False)),
                        getattr(deal, 'valid_from', None),
                        getattr(deal, 'valid_to', None)
                    )
                    conn.execute(
                        """INSERT INTO deals
                           (book_id, page_number, title, description, discount,
                            department, classified_by_llm, valid_from, valid_to)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        params
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    pass  # Duplicate deal, skip

        return inserted

    def write_to_spreadsheet(self, deals: List[Deal], output_path: Path = None) -> Path:
        """Write deals to a CSV spreadsheet organized by department."""
        if output_path is None:
            output_path = self.config.PROJECT_ROOT / "coupons_by_department.csv"

        # Sort by department then title
        sorted_deals = sorted(deals, key=lambda d: (getattr(d, 'department', 'General'), d.title))

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            f.write("Department,Title,Description,Discount,Page,Valid From,Valid To\n")
            for deal in sorted_deals:
                # Escape CSV values
                dept = getattr(deal, 'department', 'General').replace('"', '""')
                title = deal.title.replace('"', '""')
                desc = (deal.description or '').replace('"', '""').replace('\n', ' ')
                discount = (deal.discount or '').replace('"', '""')
                valid_from = getattr(deal, 'valid_from', '') or ''
                valid_to = getattr(deal, 'valid_to', '') or ''
                f.write(f'"{dept}","{title}","{desc}","{discount}",{deal.page_number},"{valid_from}","{valid_to}"\n')

        return output_path

    def _calculate_next_fetch(self, month: str, year: int) -> datetime:
        """Calculate when next fetch should occur (after validity window expires)."""
        try:
            start = datetime(year, MONTH_NAME_TO_NUM[month.lower()], 1)
            # Books typically valid ~30 days; add buffer to ensure expiration
            if month.lower() == 'december':
                return datetime(year + 1, 1, 15)
            else:
                next_month = MONTH_NAME_TO_NUM[month.lower()] + 1
                return datetime(year, next_month, 15)
        except KeyError:
            return datetime.now()

    def log_fetch(self, month: str, year: int, status: str = "completed"):
        """Log the fetch to track which books were retrieved and when."""
        book_id = self.db.find_book(year, month.lower())
        next_fetch = self._calculate_next_fetch(month, year)

        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO fetch_log (book_id, month, year, next_fetch_after, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (book_id, month.lower(), year, next_fetch.isoformat(), status)
            )

        # Also save to state file for easy access
        self._save_schedule_state(month, year, next_fetch)

    def _save_schedule_state(self, month: str, year: int, next_fetch: datetime):
        """Save schedule state to JSON file."""
        state = {
            "last_fetched_month": month.lower(),
            "last_fetched_year": year,
            "next_fetch_after": next_fetch.isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        self.schedule_state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def should_fetch_now(self) -> bool:
        """Check if we're past the expiration of the last fetched book."""
        if not self.schedule_state_path.exists():
            return True  # No state yet, can fetch

        try:
            state = json.loads(self.schedule_state_path.read_text(encoding="utf-8"))
            next_fetch = datetime.fromisoformat(state["next_fetch_after"])
            return datetime.now() >= next_fetch
        except Exception as e:
            self.logger.warning(f"Could not parse schedule state: {e}")
            return True

    def get_schedule_status(self) -> Dict[str, Any]:
        """Return current scheduling status for user notification."""
        if not self.schedule_state_path.exists():
            return {"ready_to_fetch": True, "message": "No books fetched yet. Ready to fetch."}

        state = json.loads(self.schedule_state_path.read_text(encoding="utf-8"))
        next_fetch = datetime.fromisoformat(state["next_fetch_after"])
        ready = datetime.now() >= next_fetch

        if ready:
            msg = f"Ready to fetch! Last book ({state['last_fetched_month']} {state['last_fetched_year']}) has expired."
        else:
            days_left = (next_fetch - datetime.now()).days
            msg = f"Not ready. Next fetch after {next_fetch.strftime('%B %d, %Y')} ({days_left} day(s) from now)."

        return {"ready_to_fetch": ready, "message": msg, **state}

    def plan(self, goal: str, context: dict = None):
        from ..react_loop import Plan
        return Plan(goal=goal)

    def execute(self, action: str, **kwargs) -> any:
        deals = kwargs.get('deals', [])

        if action == "classify":
            llm_count = self.classify_deals(deals)
            return {"classified": len(deals), "used_llm_fallback": llm_count}
        elif action == "write_db":
            inserted = self.write_deals_to_db(deals)
            return {"inserted": inserted}
        elif action == "write_spreadsheet":
            path = self.write_to_spreadsheet(deals)
            return {"path": str(path)}
        elif action == "log_fetch":
            month = kwargs.get('month', 'november')
            year = kwargs.get('year', 2026)
            self.log_fetch(month, year)
            return {"logged": True}
        elif action == "check_schedule":
            return self.get_schedule_status()

        return {}

    def observe(self, result: any) -> str:
        if isinstance(result, dict):
            if 'inserted' in result:
                return f"Inserted {result['inserted']} deals into database"
            elif 'classified' in result:
                return f"Classified {result['classified']} deals ({result['used_llm_fallback']} via LLM fallback)"
            elif 'ready_to_fetch' in result:
                return result.get('message', str(result))
        return str(result)

    def correct(self, assessment: str) -> Optional[str]:
        return None
