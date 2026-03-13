# ⚽ Football CLV Betting Monitor

Smart money tracking system for football betting. Monitors odds movement (CLV — Closing Line Value) across bookmakers and generates signals when lines move significantly before kickoff.

## Strategy

**Backtested on 110,000+ real Bet365 matches.**

The core insight: when bookmaker odds drop significantly before kickoff, it means sharp/smart money has come in. Betting at the **opening odds** (before the move) captures the edge.

### Signals

| Strategy | Opening Odds | CLV Threshold | Hist. WR | Hist. ROI | ~Volume |
|----------|-------------|---------------|----------|-----------|---------|
| ✈️ Away Win | 2.00-2.70 | ≥ 0.30 | 50.8% | +19.9% | ~1.2/day |
| ✈️ Away Win | 2.50-3.50 | ≥ 0.30 | 41.6% | +22.5% | ~1.6/day |
| 🏠 Home Win | 1.80-2.30 | ≥ 0.30 | 58.1% | +20.0% | ~1.0/day |

**CLV = Opening Odds − Current Odds.** If Bet365 opens Away Win at 2.80 and it drops to 2.40 before kickoff → CLV = 0.40 → signal.

### Key Finding: Away Wins

The biggest edge is in **away wins at medium-high odds** (2.0-3.5). Bookmakers systematically underadjust when smart money comes in on away teams. This was the #1 profitable market across all 110K matches and all odds ranges tested.

## Setup

```bash
# Clone
git clone https://github.com/xissay13-gif/football_script.git
cd football_script

# Install
pip install -r requirements.txt

# Get free API key (500 requests/month)
# Go to https://the-odds-api.com, sign up, copy key
cp .env.example .env
# Edit .env and paste your key

# Export key
export ODDS_API_KEY=your_key_here
```

## Usage

```bash
# Single scan — check all leagues right now
python clv_monitor.py --scan

# Continuous monitor — runs every 30 min, alerts on signals
python clv_monitor.py

# Backtest on historical data
# First, place bet365_historical.csv in data/ folder
python clv_monitor.py --backtest --backtest-file data/bet365_historical.csv
```

## How It Works

1. **Morning**: Script fetches opening odds from the-odds-api.com and saves snapshots
2. **Before kickoff** (0-3 hours): Script compares current odds to opening
3. **Signal**: If CLV ≥ 0.30 and odds are in the target range → alert
4. **Stake**: Kelly criterion calculates optimal bet size (quarter-Kelly for safety)
5. **Log**: All signals saved to `data/bet_log.csv` for P/L tracking

## File Structure

```
football_script/
├── clv_monitor.py          # Main script
├── requirements.txt
├── .env.example             # API key template
├── .gitignore
├── data/                    # Created automatically
│   ├── odds_snapshots.json  # Opening odds storage
│   └── bet_log.csv          # Bet history & P/L
└── README.md
```

## Historical Data

The backtest was run on `Bet365_Excel.xlsx` (110K+ matches, 85 columns, opening + closing odds). This file is too large for GitHub (~43MB). To run backtest locally:

1. Convert Excel to CSV: `python -c "import pandas; pandas.read_excel('Bet365_Excel.xlsx', sheet_name='Açılış Verileri').to_csv('data/bet365_historical.csv', index=False)"`
2. Run: `python clv_monitor.py --backtest`

## API Limits

[the-odds-api.com](https://the-odds-api.com) free tier: 500 requests/month. Each league scan = 1 request. With 12 leagues scanned every 30 min for ~8 hours/day = ~192 requests/day. **This exceeds the free tier.** Options:

- Scan fewer leagues (top 5 = 80/day, fits in free tier with buffer)
- Scan less frequently (every 60 min)
- Upgrade to paid plan ($20/month for 2500 requests)

## Disclaimer

This is a research tool. Past performance does not guarantee future results. Gamble responsibly.
