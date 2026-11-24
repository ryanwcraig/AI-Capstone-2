"""Supervisor agent that orchestrates the retrieval, parsing, and structuring pipeline."""

from datetime import datetime
from typing import Dict, Any, Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from ..react_loop import ReActAgent, Plan
from ..config import Config
from ..logger import get_logger
from .retrieval_agent import RetrievalAgent
from .parsing_agent import ParsingAgent
from .structuring_agent import StructuringAgent


class SupervisorAgent(ReActAgent):
    """Orchestrates the 5-action pipeline: retrieve, parse, classify, structure, schedule.

    Coordinates the three specialist agents in a sequential workflow with
    error handling and state tracking.
    """

    def __init__(self, config: Config = None):
        super().__init__("Supervisor", config)
        self.config = self.config or Config()

        # Initialize specialist agents
        self.retrieval_agent = RetrievalAgent(self.config)
        self.parsing_agent = ParsingAgent(self.config)
        self.structuring_agent = StructuringAgent(self.config)

        # Pipeline state
        self.current_deals = []
        self.current_month = None
        self.current_year = None
        self.pipeline_completed = False

    def run_pipeline(self, month: str = "november", year: int = 2026, force: bool = False) -> Dict[str, Any]:
        """Execute the complete coupon processing pipeline.

        Returns a summary of all actions performed.
        """
        self.logger.info("=" * 60)
        self.logger.info(f"  Starting Pipeline: {month.capitalize()} {year}")
        self.logger.info("=" * 60)

        self.current_month = month
        self.current_year = year
        results = {
            "month": month,
            "year": year,
            "status": "started",
            "steps": {},
            "errors": []
        }

        try:
            # ── Step 1: Check schedule before fetching
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 1: Checking if fetch is allowed")
            self.logger.info("-" * 40)

            schedule_status = self.structuring_agent.get_schedule_status()
            results["steps"]["schedule_check"] = schedule_status

            if not schedule_status["ready_to_fetch"] and not force:
                self.logger.warning(f"Fetch not allowed yet: {schedule_status['message']}")
                self.logger.info("Use force=True to override.")
                results["status"] = "skipped_not_ready"
                return results

            # ── Step 2: Retrieve coupon book
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 2: Retrieving coupon book")
            self.logger.info("-" * 40)

            fetch_result = self.retrieval_agent.fetch_coupon_book(month, year, force=force)
            results["steps"]["retrieval"] = {
                "fetched": fetch_result.get("fetched", False),
                "reason": fetch_result.get("reason", ""),
                "pages": fetch_result.get("total_pages", 0)
            }

            if not fetch_result.get("fetched", False):
                self.logger.info(f"Skipping retrieval: {fetch_result.get('reason', 'unknown')}")
                # If already downloaded, we can still parse existing images
                if fetch_result.get("reason") == "already_downloaded":
                    self.logger.info("Proceeding to parse existing images...")
                else:
                    results["status"] = "retrieval_skipped"
                    return results

            # ── Step 3: Parse deals from images
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 3: Parsing deals from images")
            self.logger.info("-" * 40)

            parse_result = self.parsing_agent.parse_book(month, year)
            self.current_deals = parse_result
            results["steps"]["parsing"] = {
                "deals_found": len(self.current_deals)
            }
            self.logger.info(f"Found {len(self.current_deals)} deals")

            if not self.current_deals:
                results["status"] = "no_deals_found"
                return results

            # ── Step 4: Classify deals
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 4: Classifying deals")
            self.logger.info("-" * 40)

            llm_count = self.structuring_agent.classify_deals(self.current_deals)
            results["steps"]["classification"] = {
                "classified": len(self.current_deals),
                "used_llm_fallback": llm_count
            }

            # ── Step 5: Write to database and spreadsheet
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 5: Writing structured output")
            self.logger.info("-" * 40)

            inserted = self.structuring_agent.write_deals_to_db(self.current_deals)
            spreadsheet_path = self.structuring_agent.write_to_spreadsheet(self.current_deals)
            results["steps"]["structuring"] = {
                "inserted_to_db": inserted,
                "spreadsheet": str(spreadsheet_path)
            }

            # ── Step 6: Log fetch and schedule next run
            self.logger.info("\n" + "-" * 40)
            self.logger.info("STEP 6: Logging fetch and scheduling")
            self.logger.info("-" * 40)

            if fetch_result.get("fetched", False):
                self.structuring_agent.log_fetch(month, year)

            schedule_status = self.structuring_agent.get_schedule_status()
            results["steps"]["scheduling"] = schedule_status

            results["status"] = "completed"
            self.pipeline_completed = True

        except Exception as e:
            results["status"] = "failed"
            results["errors"].append(str(e))
            self.logger.error(f"Pipeline failed: {e}", exc_info=True)

        # ── Summary
        self.logger.info("\n" + "=" * 60)
        self.logger.info(f"  PIPELINE COMPLETE — Status: {results['status']}")
        if results.get("steps", {}).get("parsing"):
            self.logger.info(f"  Deals found: {results['steps']['parsing'].get('deals_found', 0)}")
        if results.get("steps", {}).get("structuring"):
            self.logger.info(f"  Inserted to DB: {results['steps']['structuring'].get('inserted_to_db', 0)}")
        if results.get("errors"):
            self.logger.error(f"  Errors: {results['errors']}")
        self.logger.info("=" * 60)

        return results

    def get_report(self, month: str = "november", year: int = 2026) -> Dict[str, Any]:
        """Get a department-organized report of current deals."""
        from costco_coupon_scraper import CouponDatabase

        db = CouponDatabase(self.config.DB_PATH)
        book_id = db.find_book(year, month.lower())

        if book_id is None:
            return {"error": f"No data found for {month} {year}"}

        with self.structuring_agent._get_conn() as conn:
            rows = conn.execute(
                """SELECT department, title, description, discount, page_number
                   FROM deals WHERE book_id = ? ORDER BY department, title""",
                (book_id,)
            ).fetchall()

        report = {}
        for row in rows:
            dept = row["department"]
            if dept not in report:
                report[dept] = []
            report[dept].append({
                "title": row["title"],
                "description": row["description"],
                "discount": row["discount"],
                "page": row["page_number"]
            })

        return {
            "month": month,
            "year": year,
            "departments": report,
            "total_deals": len(rows)
        }

    def plan(self, goal: str, context: dict = None):
        return Plan(goal=goal)

    def execute(self, action: str, **kwargs) -> any:
        if action == "run_pipeline":
            month = kwargs.get('month', 'november')
            year = kwargs.get('year', 2026)
            force = kwargs.get('force', False)
            return self.run_pipeline(month, year, force)
        elif action == "get_report":
            month = kwargs.get('month', 'november')
            year = kwargs.get('year', 2026)
            return self.get_report(month, year)
        return {}

    def observe(self, result: any) -> str:
        if isinstance(result, dict):
            if 'status' in result:
                return f"Pipeline status: {result['status']}"
        return str(result)

    def correct(self, assessment: str) -> Optional[str]:
        return None


def main():
    """CLI entry point for running the agent."""
    import argparse

    parser = argparse.ArgumentParser(description="Costco Coupon Agent")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run pipeline command
    run_parser = subparsers.add_parser("run", help="Run the complete processing pipeline")
    run_parser.add_argument("--month", default="november", help="Coupon book month")
    run_parser.add_argument("--year", type=int, default=2026, help="Coupon book year")
    run_parser.add_argument("--force", action="store_true", help="Force fetch even if not ready")

    # Report command
    report_parser = subparsers.add_parser("report", help="Show department-organized deal report")
    report_parser.add_argument("--month", default="november", help="Coupon book month")
    report_parser.add_argument("--year", type=int, default=2026, help="Coupon book year")

    # Status command
    subparsers.add_parser("status", help="Show current scheduling status")

    args = parser.parse_args()

    config = Config()
    supervisor = SupervisorAgent(config)

    if args.command == "run":
        supervisor.run_pipeline(args.month, args.year, force=args.force)
    elif args.command == "report":
        report = supervisor.get_report(args.month, args.year)
        if "error" in report:
            print(f"Error: {report['error']}")
            return

        print(f"\n{'='*70}")
        print(f"  Costco Coupon Deals — {args.month.capitalize()} {args.year}")
        print(f"  Total deals: {report['total_deals']}")
        print(f"{'='*70}\n")

        for dept, deals in report["departments"].items():
            print(f"\n📌 {dept.upper()} ({len(deals)} deals)")
            print("-" * 40)
            for deal in deals[:10]:  # Show first 10 per department
                discount = f" [{deal['discount']}]" if deal["discount"] else ""
                print(f"  • {deal['title']}{discount} (page {deal['page']})")
            if len(deals) > 10:
                print(f"  ... and {len(deals) - 10} more")

    elif args.command == "status":
        status = supervisor.structuring_agent.get_schedule_status()
        print("\nSchedule Status:")
        for key, value in status.items():
            print(f"  {key}: {value}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
