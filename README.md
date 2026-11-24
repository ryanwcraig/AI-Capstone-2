# Costco Coupon Agent

A personal assistant agent that automatically retrieves, parses, classifies, and schedules Costco coupon book data from costcoinsider.com.

## Features

- **Automated retrieval** — Fetches new coupon books with rate-limit protections
- **Apple Vision OCR** — Fast, free, offline text extraction using macOS native capabilities
- **Smart classification** — Department categorization via keyword rules + LLM fallback
- **Structured output** — SQLite database and CSV spreadsheet organized by department
- **Intelligent scheduling** — Only fetches after validity window expiration

## Setup

```bash
# Activate virtual environment
source .venv/bin/activate

# Install dependencies (if needed)
pip install -r requirements.txt
```

> **Note:** Requires macOS for Apple Vision OCR.

## Usage

### Run the Complete Pipeline

```bash
python main.py run --month november --year 2026
```

This performs all 5 pipeline actions: schedule check → retrieval → parsing → classification → structuring.

### Force Fetch (Override Schedule)

```bash
python main.py run --month november --year 2026 --force
```

### View Department Report

```bash
python main.py report --month november --year 2026
```

### Check Schedule Status

```bash
python main.py status
```

## Pipeline Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Supervisor Agent                          │
│  (Orchestrates the complete workflow)                        │
├──────────────┬──────────────────┬───────────────────────────┤
│              │                  │                           │
▼              ▼                  ▼                           ▼
┌──────────┐ ┌──────────┐   ┌──────────────┐   ┌────────────┐ │
│Retrieval │ │ Parsing  │   │ Structuring │   │   Schedule │ │
│   Agent  │ │  Agent   │   │    Agent     │   │   State    │ │
└──────────┘ └──────────┘   └──────────────┘   └────────────┘ │
```

### The 5 Actions

1. **Retrieval** — Fetch coupon book from costcoinsider.com (with domain allowlist, date filtering Jan 2026+, caching)
2. **Parsing** — Extract deal text via Apple Vision OCR + segmentation
3. **Classification** — Assign deals to departments using keyword rules + LLM fallback
4. **Structuring** — Write to SQLite database and CSV spreadsheet
5. **Scheduling** — Log fetch, calculate next allowed fetch time

## Rate-Limit Protections

- Domain allowlist: costcoinsider.com only
- Cache-first strategy (session + disk)
- 2-3 second randomized delay between requests
- Exponential backoff on failures
- Time scope: January 2026 and later only
- Single-pass per validity window

## Department Categories

| Department | Example Keywords |
|------------|------------------|
| Grocery | food, snack, coffee, cereal, pasta, juice |
| Health & Beauty | vitamin, shampoo, lotion, toothpaste |
| Home & Kitchen | cookware, utensil, cleaning, furniture |
| Electronics | phone, laptop, tv, headphone |
| Apparel | shirt, pants, dress, jacket, shoe |
| Automotive | oil, tire, wiper, fuel, motor |
| Seasonal | christmas, holiday, halloween, easter |
| Dining | restaurant, dinner, coffee shop, pizza |
| Travel | hotel, resort, airline, cruise |
| General | (fallback for unmatched deals) |

## Output Files

| File | Description |
|------|-------------|
| `coupons.db` | SQLite database with books, pages, deals, fetch logs |
| `coupons_by_department.csv` | Spreadsheet of deals organized by department |
| `schedule_state.json` | Tracks last fetched book and next fetch time |
| `classification_feedback.json` | User classification feedback for learning |
| `coupons/` | Directory of downloaded coupon book images |

## Project Structure

```
CostcoCouponAgent/
├── main.py                    # Entry point
├── costco_coupon_scraper.py   # Original scraper (modified with date filter)
├── agent/
│   ├── __init__.py            # Package initialization
│   ├── config.py              # Central configuration
│   ├── logger.py              # Logging utilities
│   ├── ocr.py                 # Apple Vision OCR service
│   ├── classifier.py          # Hybrid deal classification
│   ├── react_loop.py          # ReAct reasoning loop framework
│   └── agents/
│       ├── __init__.py        # Agents package
│       ├── supervisor.py      # Supervisor agent (orchestrator)
│       ├── retrieval_agent.py # Retrieval specialist
│       ├── parsing_agent.py   # Parsing specialist
│       └── structuring_agent.py # Structuring specialist
├── coupons/                   # Downloaded coupon images
├── cache/                     # OCR and HTML caches
└── requirements.txt           # Python dependencies
```
