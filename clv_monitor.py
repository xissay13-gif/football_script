#!/usr/bin/env python3
"""
⚽ Football CLV (Closing Line Value) Betting Model
====================================================
Monitors odds movement across bookmakers and generates signals
when smart money moves the line significantly.

Strategy (backtested on 110K+ Bet365 matches):
- Away Win: CLV >= 0.30, opening odds 2.00-3.50 → ROI +20-22%
- Home Win: CLV >= 0.30, opening odds 1.80-2.30 → ROI +20%
- Volume: ~1-3 signals per day

Usage:
    python clv_monitor.py                     # Run monitor (continuous)
    python clv_monitor.py --scan              # Single scan, show signals
    python clv_monitor.py --backtest          # Validate on historical data
    python clv_monitor.py --backtest-file data/bet365_historical.csv

Environment:
    ODDS_API_KEY=your_key_here                # from the-odds-api.com
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ============================================================================
# CONFIG
# ============================================================================

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Supported leagues (the-odds-api sport keys)
LEAGUES = {
    "soccer_epl": "🏴 EPL",
    "soccer_spain_la_liga": "🇪🇸 LaLiga",
    "soccer_germany_bundesliga": "🇩🇪 Bundesliga",
    "soccer_italy_serie_a": "🇮🇹 Serie A",
    "soccer_france_ligue_one": "🇫🇷 Ligue 1",
    "soccer_netherlands_eredivisie": "🇳🇱 Eredivisie",
    "soccer_portugal_primeira_liga": "🇵🇹 Liga Portugal",
    "soccer_turkey_super_league": "🇹🇷 Süper Lig",
    "soccer_efl_champ": "🏴 Championship",
    "soccer_usa_mls": "🇺🇸 MLS",
    "soccer_brazil_campeonato": "🇧🇷 Serie A",
    "soccer_mexico_ligamx": "🇲🇽 Liga MX",
}

# Strategy parameters (from 110K match backtest)
STRATEGIES = {
    "away_win": {
        "name": "AWAY WIN",
        "emoji": "✈️",
        "clv_min": 0.30,
        "odds_ranges": [
            {"min": 2.00, "max": 2.70, "label": "medium", "hist_wr": 0.508, "hist_roi": 19.9},
            {"min": 2.50, "max": 3.50, "label": "high", "hist_wr": 0.416, "hist_roi": 22.5},
        ],
    },
    "home_win": {
        "name": "HOME WIN",
        "emoji": "🏠",
        "clv_min": 0.30,
        "odds_ranges": [
            {"min": 1.80, "max": 2.30, "label": "standard", "hist_wr": 0.581, "hist_roi": 20.0},
            {"min": 2.00, "max": 2.70, "label": "value", "hist_wr": 0.506, "hist_roi": 15.8},
        ],
    },
}

# Kelly criterion
KELLY_FRACTION = 0.25  # quarter-Kelly for safety
DEFAULT_BANKROLL = 1000

# File paths
DATA_DIR = Path("data")
LOG_FILE = DATA_DIR / "bet_log.csv"
SNAPSHOTS_FILE = DATA_DIR / "odds_snapshots.json"

# Scan interval
SCAN_INTERVAL_MINUTES = 30


# ============================================================================
# ODDS API
# ============================================================================

def fetch_odds(sport: str, bookmakers: str = "bet365,pinnacle,williamhill") -> list:
    """Fetch current odds from the-odds-api.com."""
    if not ODDS_API_KEY:
        print("  ⚠️  ODDS_API_KEY not set. Get free key at https://the-odds-api.com")
        return []

    url = f"{ODDS_API_BASE}/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "uk,eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
        "bookmakers": bookmakers,
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 401:
            print("  ❌ Invalid API key")
            return []
        if resp.status_code == 429:
            print("  ❌ API rate limit hit. Wait or upgrade plan.")
            return []
        resp.raise_for_status()

        # Track remaining requests
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        print(f"  📡 API: {remaining} requests remaining ({used} used)")

        return resp.json()
    except requests.RequestException as e:
        print(f"  ❌ API error: {e}")
        return []


def parse_odds(events: list) -> list:
    """Parse API response into clean match data."""
    matches = []
    for event in events:
        match = {
            "id": event["id"],
            "sport": event["sport_key"],
            "league": LEAGUES.get(event["sport_key"], event["sport_key"]),
            "home": event["home_team"],
            "away": event["away_team"],
            "kickoff": event["commence_time"],
            "bookmakers": {},
        }

        for bm in event.get("bookmakers", []):
            bm_name = bm["key"]
            for market in bm.get("markets", []):
                if market["key"] == "h2h":
                    outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                    match["bookmakers"][bm_name] = {
                        "home": outcomes.get(event["home_team"], 0),
                        "draw": outcomes.get("Draw", 0),
                        "away": outcomes.get(event["away_team"], 0),
                        "updated": bm["last_update"],
                    }

        if match["bookmakers"]:
            matches.append(match)

    return matches


# ============================================================================
# SNAPSHOT MANAGEMENT
# ============================================================================

def load_snapshots() -> dict:
    """Load saved opening odds snapshots."""
    if SNAPSHOTS_FILE.exists():
        with open(SNAPSHOTS_FILE) as f:
            return json.load(f)
    return {}


def save_snapshots(snapshots: dict):
    """Save odds snapshots."""
    DATA_DIR.mkdir(exist_ok=True)
    with open(SNAPSHOTS_FILE, "w") as f:
        json.dump(snapshots, f, indent=2)


def update_snapshots(matches: list, snapshots: dict) -> dict:
    """
    Record opening odds for new matches.
    Only saves the FIRST odds seen (opening line).
    """
    for match in matches:
        match_key = f"{match['home']}_{match['away']}_{match['kickoff']}"

        if match_key not in snapshots:
            # First time seeing this match — record opening odds
            best_bm = _get_best_bookmaker(match)
            if best_bm:
                snapshots[match_key] = {
                    "id": match["id"],
                    "home": match["home"],
                    "away": match["away"],
                    "league": match["league"],
                    "sport": match["sport"],
                    "kickoff": match["kickoff"],
                    "opening": best_bm,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }

    save_snapshots(snapshots)
    return snapshots


def _get_best_bookmaker(match: dict) -> dict:
    """Get odds from best available bookmaker (prefer bet365 > pinnacle)."""
    for bm in ["bet365", "pinnacle", "williamhill"]:
        if bm in match["bookmakers"]:
            return match["bookmakers"][bm]
    # Fallback: first available
    if match["bookmakers"]:
        return list(match["bookmakers"].values())[0]
    return {}


# ============================================================================
# CLV CALCULATION & SIGNAL DETECTION
# ============================================================================

def calculate_clv(opening: dict, current: dict) -> dict:
    """
    Calculate CLV for all outcomes.
    Positive CLV = line moved toward this outcome (smart money).
    """
    return {
        "home": opening.get("home", 0) - current.get("home", 0),
        "draw": opening.get("draw", 0) - current.get("draw", 0),
        "away": opening.get("away", 0) - current.get("away", 0),
    }


def check_signals(matches: list, snapshots: dict) -> list:
    """
    Check all matches for CLV signals.
    Returns list of actionable signals.
    """
    signals = []
    now = datetime.now(timezone.utc)

    for match in matches:
        match_key = f"{match['home']}_{match['away']}_{match['kickoff']}"
        snapshot = snapshots.get(match_key)

        if not snapshot:
            continue

        # Only check matches within 2 hours of kickoff
        try:
            kickoff = datetime.fromisoformat(match["kickoff"].replace("Z", "+00:00"))
        except:
            continue

        hours_to_kick = (kickoff - now).total_seconds() / 3600
        if hours_to_kick < 0 or hours_to_kick > 3:
            continue

        # Get current best odds
        current = _get_best_bookmaker(match)
        if not current:
            continue

        opening = snapshot["opening"]
        clv = calculate_clv(opening, current)

        # Check Away Win signal
        for strat_key, strat in STRATEGIES.items():
            if strat_key == "away_win":
                opening_odds = opening.get("away", 0)
                current_odds = current.get("away", 0)
                clv_value = clv["away"]
                outcome_label = f"{match['away']} (away)"
            elif strat_key == "home_win":
                opening_odds = opening.get("home", 0)
                current_odds = current.get("home", 0)
                clv_value = clv["home"]
                outcome_label = f"{match['home']} (home)"
            else:
                continue

            if clv_value < strat["clv_min"]:
                continue

            # Check if opening odds fit any range
            for odds_range in strat["odds_ranges"]:
                if odds_range["min"] <= opening_odds <= odds_range["max"]:
                    # Calculate Kelly stake
                    implied_prob = 1 / opening_odds
                    estimated_prob = implied_prob + (clv_value * 0.3)  # rough adjustment
                    estimated_prob = min(estimated_prob, 0.75)
                    kelly = _kelly_criterion(estimated_prob, opening_odds)

                    signals.append({
                        "match_key": match_key,
                        "strategy": strat_key,
                        "emoji": strat["emoji"],
                        "name": strat["name"],
                        "league": match["league"],
                        "home": match["home"],
                        "away": match["away"],
                        "pick": outcome_label,
                        "opening_odds": round(opening_odds, 2),
                        "current_odds": round(current_odds, 2),
                        "clv": round(clv_value, 3),
                        "kickoff": match["kickoff"],
                        "hours_to_kick": round(hours_to_kick, 1),
                        "kelly_pct": round(kelly * 100, 1),
                        "hist_wr": odds_range["hist_wr"],
                        "hist_roi": odds_range["hist_roi"],
                        "odds_label": odds_range["label"],
                    })
                    break  # don't double-count

    # Sort by CLV descending
    signals.sort(key=lambda x: x["clv"], reverse=True)
    return signals


def _kelly_criterion(prob: float, odds: float, fraction: float = KELLY_FRACTION) -> float:
    """Calculate fractional Kelly stake as % of bankroll."""
    edge = prob * odds - 1
    if edge <= 0:
        return 0
    kelly = edge / (odds - 1)
    return max(0, min(kelly * fraction, 0.10))  # cap at 10%


# ============================================================================
# LOGGING
# ============================================================================

def log_signal(signal: dict, bankroll: float = DEFAULT_BANKROLL):
    """Log a signal to CSV for P/L tracking."""
    DATA_DIR.mkdir(exist_ok=True)
    file_exists = LOG_FILE.exists()

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp", "league", "home", "away", "strategy", "pick",
            "opening_odds", "current_odds", "clv", "kelly_pct", "stake_units",
            "kickoff", "result", "pnl",
        ])
        if not file_exists:
            writer.writeheader()

        stake = round(bankroll * signal["kelly_pct"] / 100, 2)
        writer.writerow({
            "timestamp": datetime.now().isoformat(),
            "league": signal["league"],
            "home": signal["home"],
            "away": signal["away"],
            "strategy": signal["strategy"],
            "pick": signal["pick"],
            "opening_odds": signal["opening_odds"],
            "current_odds": signal["current_odds"],
            "clv": signal["clv"],
            "kelly_pct": signal["kelly_pct"],
            "stake_units": stake,
            "kickoff": signal["kickoff"],
            "result": "",  # fill manually after match
            "pnl": "",
        })


# ============================================================================
# BACKTEST ON HISTORICAL DATA
# ============================================================================

def run_backtest(csv_path: str):
    """Validate strategy on historical Bet365 data."""
    import pandas as pd

    print(f"\n  📂 Loading {csv_path}...")
    df = pd.read_csv(csv_path)
    print(f"  Loaded {len(df)} matches, {df.columns.size} columns")

    # Parse scores
    df["home_g"] = df["счет матча"].str.split("-").str[0].astype(float, errors="ignore")
    df["away_g"] = df["счет матча"].str.split("-").str[1].astype(float, errors="ignore")
    df["home_win"] = df["home_g"] > df["away_g"]
    df["away_win"] = df["away_g"] > df["home_g"]

    # CLVs
    df["clv_h"] = df["П1"] - df["П1 ЗАКР"]
    df["clv_a"] = df["П2"] - df["П2 ЗАКР"]

    print(f"\n{'='*65}")
    print(f"  📊 BACKTEST RESULTS")
    print(f"{'='*65}")

    for strat_key, strat in STRATEGIES.items():
        if strat_key == "away_win":
            clv_col, odds_col, outcome_col = "clv_a", "П2", "away_win"
        else:
            clv_col, odds_col, outcome_col = "clv_h", "П1", "home_win"

        print(f"\n  {strat['emoji']} {strat['name']} (CLV >= {strat['clv_min']}):")

        for rng in strat["odds_ranges"]:
            m = (
                (df[clv_col] >= strat["clv_min"])
                & (df[odds_col] >= rng["min"])
                & (df[odds_col] <= rng["max"])
            )
            s = df[m].dropna(subset=[outcome_col])
            if len(s) == 0:
                continue

            wr = s[outcome_col].mean()
            roi = (s[outcome_col] * s[odds_col] - 1).mean() * 100
            pnl = (s[outcome_col] * s[odds_col] - 1).sum()
            avg_odds = s[odds_col].mean()
            vol = len(s) / len(df) * 40

            print(f"    Odds {rng['min']}-{rng['max']} ({rng['label']}):")
            print(f"      Matches:   {len(s)}")
            print(f"      Win rate:  {wr:.1%}")
            print(f"      Avg odds:  {avg_odds:.2f}")
            print(f"      ROI:       {roi:+.1f}%")
            print(f"      P/L:       {pnl:+.0f}u")
            print(f"      ~Volume:   {vol:.1f}/day")

    # Combined
    print(f"\n  📈 COMBINED (all strategies):")
    m_away = (df["clv_a"] >= 0.30) & (df["П2"] >= 2.00) & (df["П2"] <= 3.50)
    m_home = (df["clv_h"] >= 0.30) & (df["П1"] >= 1.80) & (df["П1"] <= 2.30)

    for label, mask, odds_col, out_col in [
        ("Away pool", m_away, "П2", "away_win"),
        ("Home pool", m_home, "П1", "home_win"),
    ]:
        s = df[mask].dropna(subset=[out_col])
        if len(s) > 0:
            roi = (s[out_col] * s[odds_col] - 1).mean() * 100
            pnl = (s[out_col] * s[odds_col] - 1).sum()
            print(f"    {label}: {len(s)} bets, ROI={roi:+.1f}%, P/L={pnl:+.0f}u")

    total_bets = m_away.sum() + m_home.sum()
    print(f"    Total volume: ~{total_bets / len(df) * 40:.1f} bets/day")


# ============================================================================
# DISPLAY
# ============================================================================

def print_header():
    print()
    print("=" * 65)
    print("  ⚽ CLV BETTING MONITOR v1.0")
    print("  Smart money tracking | Backtested on 110K+ matches")
    print("=" * 65)


def print_signals(signals: list):
    if not signals:
        print("\n  😴 No signals right now. Checking again soon...")
        return

    print(f"\n  🚨 {len(signals)} SIGNAL(S) DETECTED:\n")

    for i, s in enumerate(signals, 1):
        print(f"  {'─'*60}")
        print(f"  #{i} {s['emoji']} {s['name']} | {s['league']}")
        print(f"  {s['home']} vs {s['away']}")
        print(f"  👉 PICK: {s['pick']}")
        print(f"  📊 Opening: {s['opening_odds']} → Current: {s['current_odds']} (CLV: {s['clv']:+.2f})")
        print(f"  ⏰ Kickoff in {s['hours_to_kick']}h")
        print(f"  💰 Kelly stake: {s['kelly_pct']}% of bankroll")
        print(f"  📈 Historical: WR={s['hist_wr']:.0%}, ROI=+{s['hist_roi']:.0f}%")

    print(f"  {'─'*60}\n")


def print_status(snapshots: dict):
    """Print current monitoring status."""
    now = datetime.now(timezone.utc)
    active = 0
    for key, snap in snapshots.items():
        try:
            ko = datetime.fromisoformat(snap["kickoff"].replace("Z", "+00:00"))
            if ko > now:
                active += 1
        except:
            pass
    print(f"  📋 Tracking {active} upcoming matches")
    print(f"  📁 Log: {LOG_FILE}")
    print(f"  💾 Snapshots: {SNAPSHOTS_FILE}")


# ============================================================================
# MAIN LOOPS
# ============================================================================

def single_scan():
    """Run a single scan across all leagues."""
    print_header()

    snapshots = load_snapshots()
    all_signals = []

    for sport_key, league_name in LEAGUES.items():
        print(f"\n  🔍 Scanning {league_name}...")
        events = fetch_odds(sport_key)
        if not events:
            continue

        matches = parse_odds(events)
        print(f"     Found {len(matches)} matches with odds")

        snapshots = update_snapshots(matches, snapshots)
        signals = check_signals(matches, snapshots)
        all_signals.extend(signals)

    print_signals(all_signals)
    print_status(snapshots)

    # Log signals
    for signal in all_signals:
        log_signal(signal)

    return all_signals


def continuous_monitor():
    """Run continuous monitoring loop."""
    print_header()
    print(f"\n  🔄 Starting continuous monitor (every {SCAN_INTERVAL_MINUTES} min)")
    print(f"  Press Ctrl+C to stop\n")

    while True:
        try:
            signals = []
            snapshots = load_snapshots()

            for sport_key, league_name in LEAGUES.items():
                events = fetch_odds(sport_key)
                if not events:
                    continue

                matches = parse_odds(events)
                snapshots = update_snapshots(matches, snapshots)
                sigs = check_signals(matches, snapshots)
                signals.extend(sigs)

            now = datetime.now().strftime("%H:%M:%S")
            if signals:
                print(f"\n  [{now}] 🚨 {len(signals)} SIGNAL(S)!")
                print_signals(signals)
                for s in signals:
                    log_signal(s)
            else:
                active = sum(1 for s in snapshots.values()
                             if datetime.fromisoformat(
                                 s["kickoff"].replace("Z", "+00:00")
                             ) > datetime.now(timezone.utc))
                print(f"  [{now}] No signals. Tracking {active} matches. Next scan in {SCAN_INTERVAL_MINUTES}m")

            time.sleep(SCAN_INTERVAL_MINUTES * 60)

        except KeyboardInterrupt:
            print("\n\n  ✋ Monitor stopped.")
            break
        except Exception as e:
            print(f"  ❌ Error: {e}. Retrying in 60s...")
            time.sleep(60)


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Football CLV Monitor")
    parser.add_argument("--scan", action="store_true", help="Single scan")
    parser.add_argument("--backtest", action="store_true", help="Run backtest")
    parser.add_argument("--backtest-file", type=str, default="data/bet365_historical.csv",
                        help="Path to historical CSV")
    parser.add_argument("--bankroll", type=float, default=DEFAULT_BANKROLL,
                        help="Bankroll for Kelly sizing")
    args = parser.parse_args()

    DATA_DIR.mkdir(exist_ok=True)

    if args.backtest:
        run_backtest(args.backtest_file)
    elif args.scan:
        single_scan()
    else:
        continuous_monitor()


if __name__ == "__main__":
    main()
