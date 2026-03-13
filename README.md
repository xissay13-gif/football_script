# ⚽ Football CLV Monitor v2.0 — Free Tier

Smart money tracking for football betting. Monitors odds movement (CLV) and generates signals.

**Backtested on 110,000+ Bet365 matches.**

## Architecture

```
Flashscore (FREE)          the-odds-api (FREE tier)
224 leagues, 300+ matches   50 leagues, 500 req/month
     │ schedule                   │ live odds
     └──────────┐    ┌────────────┘
                ▼    ▼
         CLV MONITOR v2.0
         ┌─────────────────┐
         │ 1. Get schedule  │ ← free, no limit
         │ 2. Which start   │
         │    in <3 hours?  │
         │ 3. Fetch odds    │ ← only for those leagues
         │    for THOSE     │   (saves API budget)
         │ 4. Compare open  │
         │    vs current    │
         │ 5. CLV >= 0.30?  │
         │    → SIGNAL      │
         └─────────────────┘
```

## Strategy

| Signal | Opening Odds | CLV | WR | ROI | ~Volume |
|--------|-------------|-----|-----|-----|---------|
| ✈️ Away Win | 2.00-2.70 | ≥0.30 | 50.8% | +19.9% | ~1.2/day |
| ✈️ Away Win | 2.50-3.50 | ≥0.30 | 41.6% | +22.5% | ~1.6/day |
| 🏠 Home Win | 1.80-2.30 | ≥0.30 | 58.1% | +20.0% | ~1.0/day |

**CLV** = Opening Odds − Current Odds. Positive = smart money moved the line.

## Setup

```bash
git clone https://github.com/xissay13-gif/football_script.git
cd football_script
pip install -r requirements.txt
export ODDS_API_KEY=your_key    # from the-odds-api.com (free)
```

## Usage

```bash
# See today's schedule (FREE, no API calls)
python clv_monitor.py --schedule

# Single scan (uses ~4-6 API requests)
python clv_monitor.py --scan

# Continuous monitor (scans every 30 min)
python clv_monitor.py

# Check API budget
python clv_monitor.py --budget

# Backtest on historical data
python clv_monitor.py --backtest --backtest-file data/bet365_historical.csv
```

## Budget Optimization

Free tier = 500 requests/month. The script only fetches odds for leagues that have matches starting within 3 hours (not all 50 leagues every time). Typical usage: 4-8 requests per scan × 2-3 scans per day = **~20 requests/day = 600/month**.

Tips to stay under 500:
- Run `--schedule` first (free) to see when matches are
- Scan only during peak hours (14:00-22:00 CET)
- Register a second free account → `export ODDS_API_KEY_2=second_key`

## Files

```
├── clv_monitor.py           # Main script
├── requirements.txt
├── .env.example
├── data/
│   ├── odds_snapshots.json  # Opening odds (auto-created)
│   ├── bet_log.csv          # Signal history (auto-created)
│   └── api_budget.json      # Usage tracking (auto-created)
└── README.md
```
