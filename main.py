#!/usr/bin/env python3
"""
Costco Coupon Agent - Main Entry Point

A personal assistant agent that automatically retrieves, parses, classifies,
and schedules Costco coupon book data from costcoinsider.com.

Usage:
    # Run the complete pipeline (fetch, parse, classify, structure, schedule)
    python main.py run --month november --year 2026

    # Force fetch even if not ready
    python main.py run --month november --year 2026 --force

    # View department-organized report of deals
    python main.py report --month november --year 2026

    # Check scheduling status
    python main.py status
"""

from agent.agents.supervisor import SupervisorAgent, main as supervisor_main
from agent.config import Config


def main():
    """Entry point for the Costco Coupon Agent."""
    supervisor_main()


if __name__ == "__main__":
    main()
