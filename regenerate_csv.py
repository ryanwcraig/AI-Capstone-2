#!/usr/bin/env python3
"""Regenerate the CSV spreadsheet from the database, including validity dates."""

import sqlite3
from pathlib import Path

config_root = Path(__file__).resolve().parent
db_path = config_root / "coupons.db"
output_path = config_root / "coupons_by_department.csv"

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Query deals with validity dates, sorted by department then title
rows = conn.execute("""
    SELECT department, title, description, discount, page_number, valid_from, valid_to
    FROM deals
    ORDER BY department, title
""").fetchall()

with open(output_path, "w", newline="", encoding="utf-8") as f:
    f.write("Department,Title,Description,Discount,Page,Valid From,Valid To\n")
    for row in rows:
        dept = row["department"].replace('"', '""')
        title = row["title"].replace('"', '""')
        desc = (row["description"] or "").replace('"', '""').replace("\n", " ")
        discount = (row["discount"] or "").replace('"', '""')
        valid_from = row["valid_from"] or ""
        valid_to = row["valid_to"] or ""
        f.write(f'"{dept}","{title}","{desc}","{discount}",{row["page_number"]},"{valid_from}","{valid_to}"\n')

print(f"Wrote {len(rows)} deals to CSV")
conn.close()
