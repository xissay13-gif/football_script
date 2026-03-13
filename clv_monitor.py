#!/usr/bin/env python3
"""
Football CLV Monitor v2.0 - Free Tier Optimized
Flashscore (224 leagues, free schedule) + the-odds-api (50 leagues, targeted odds)
"""
import argparse, csv, json, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

ODDS_API_KEYS = [k for k in [os.environ.get("ODDS_API_KEY",""), os.environ.get("ODDS_API_KEY_2","")] if k]
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
FLASHSCORE_FEED = "https://www.flashscore.com/x/feed/f_1_0_1_en-gb_1"

ODDS_API_LEAGUES = {
    "soccer_epl":"ENGLAND: Premier League","soccer_efl_champ":"ENGLAND: Championship",
    "soccer_england_league1":"ENGLAND: League 1","soccer_england_league2":"ENGLAND: League 2",
    "soccer_spain_la_liga":"SPAIN: LaLiga","soccer_spain_segunda_division":"SPAIN: LaLiga 2",
    "soccer_germany_bundesliga":"GERMANY: Bundesliga","soccer_germany_bundesliga2":"GERMANY: 2. Bundesliga",
    "soccer_germany_liga3":"GERMANY: 3. Liga","soccer_italy_serie_a":"ITALY: Serie A",
    "soccer_italy_serie_b":"ITALY: Serie B","soccer_france_ligue_one":"FRANCE: Ligue 1",
    "soccer_france_ligue_two":"FRANCE: Ligue 2","soccer_netherlands_eredivisie":"NETHERLANDS: Eredivisie",
    "soccer_portugal_primeira_liga":"PORTUGAL: Primeira Liga","soccer_turkey_super_league":"TURKEY: Super Lig",
    "soccer_belgium_first_div":"BELGIUM: First Div","soccer_denmark_superliga":"DENMARK: Superliga",
    "soccer_sweden_allsvenskan":"SWEDEN: Allsvenskan","soccer_norway_eliteserien":"NORWAY: Eliteserien",
    "soccer_austria_bundesliga":"AUSTRIA: Bundesliga","soccer_switzerland_superleague":"SWITZERLAND: Super League",
    "soccer_greece_super_league":"GREECE: Super League","soccer_poland_ekstraklasa":"POLAND: Ekstraklasa",
    "soccer_spl":"SCOTLAND: Premiership","soccer_russia_premier_league":"RUSSIA: Premier League",
    "soccer_usa_mls":"USA: MLS","soccer_mexico_ligamx":"MEXICO: Liga MX",
    "soccer_brazil_campeonato":"BRAZIL: Serie A","soccer_brazil_serie_b":"BRAZIL: Serie B",
    "soccer_argentina_primera_division":"ARGENTINA: Primera Division",
    "soccer_chile_campeonato":"CHILE: Primera Division","soccer_japan_j_league":"JAPAN: J1 League",
    "soccer_korea_kleague1":"KOREA: K League 1","soccer_china_superleague":"CHINA: Super League",
    "soccer_australia_aleague":"AUSTRALIA: A-League","soccer_saudi_arabia_pro_league":"SAUDI ARABIA: Pro League",
    "soccer_league_of_ireland":"IRELAND: Premier Division",
    "soccer_uefa_champs_league":"EUROPE: Champions League",
    "soccer_uefa_europa_league":"EUROPE: Europa League",
    "soccer_uefa_europa_conference_league":"EUROPE: Conference League",
    "soccer_fa_cup":"ENGLAND: FA Cup","soccer_spain_copa_del_rey":"SPAIN: Copa del Rey",
    "soccer_germany_dfb_pokal":"GERMANY: DFB-Pokal","soccer_france_coupe_de_france":"FRANCE: Coupe de France",
}

STRATEGIES = {
    "away_win":{"name":"AWAY WIN","emoji":"✈️","clv_min":0.30,"ranges":[
        {"min":1.80,"max":2.20,"hist_wr":0.593,"hist_roi":19.4},
        {"min":2.00,"max":2.70,"hist_wr":0.508,"hist_roi":19.9},
        {"min":2.50,"max":3.50,"hist_wr":0.416,"hist_roi":22.5}]},
    "home_win":{"name":"HOME WIN","emoji":"🏠","clv_min":0.30,"ranges":[
        {"min":1.80,"max":2.30,"hist_wr":0.581,"hist_roi":20.0},
        {"min":2.00,"max":2.70,"hist_wr":0.506,"hist_roi":15.8}]},
}

DATA_DIR = Path("data"); LOG_FILE = DATA_DIR/"bet_log.csv"
SNAPSHOTS_FILE = DATA_DIR/"odds_snapshots.json"; BUDGET_FILE = DATA_DIR/"api_budget.json"
SCAN_INTERVAL = 30; HOURS_WINDOW = 3; KELLY_FRAC = 0.25

# ── Flashscore ──
def fetch_schedule():
    headers = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36","X-Fsign":"SW9D1eZo"}
    try:
        r = requests.get(FLASHSCORE_FEED, headers=headers, timeout=15)
        if r.status_code != 200 or len(r.text) < 100: return []
    except: return []
    matches, league, now = [], None, time.time()
    for line in r.text.split("~"):
        f = {}
        for p in line.split("\xac"):
            if "\xf7" in p: k,v = p.split("\xf7",1); f[k]=v
        if "ZA" in f: league = f["ZA"]
        if "AA" in f and "AE" in f:
            ts = int(f.get("AD","0"))
            if ts > now:
                matches.append({"fs_id":f["AA"],"home":f.get("AE",""),"away":f.get("AF",""),
                    "league":league or "","kickoff_ts":ts,
                    "kickoff":datetime.fromtimestamp(ts,tz=timezone.utc).isoformat(),
                    "hours_until":(ts-now)/3600})
    return matches

def match_to_api(league):
    if not league: return ""
    u = league.upper()
    for k,v in ODDS_API_LEAGUES.items():
        parts = v.upper().split(": ")
        if len(parts)==2 and parts[0] in u and parts[1] in u: return k
    return ""

# ── Odds API ──
def fetch_odds(sport):
    key = next((k for k in ODDS_API_KEYS if k), "")
    if not key: return []
    try:
        r = requests.get(f"{ODDS_API_BASE}/sports/{sport}/odds",
            params={"apiKey":key,"regions":"uk,eu","markets":"h2h","oddsFormat":"decimal"}, timeout=15)
        if r.status_code in (401,429,404): return []
        r.raise_for_status()
        rem = r.headers.get("x-requests-remaining","?")
        _save_budget(rem)
        return r.json()
    except: return []

def parse_odds(events):
    out = {}
    for e in events:
        h, a = e["home_team"], e["away_team"]
        for bm in e.get("bookmakers",[]):
            for mkt in bm.get("markets",[]):
                if mkt["key"]=="h2h":
                    oc = {o["name"]:o["price"] for o in mkt["outcomes"]}
                    k = f"{h}|{a}|{e['commence_time']}"
                    if k not in out:
                        out[k] = {"home":h,"away":a,"kickoff":e["commence_time"],"bm":bm["key"],
                            "home_odds":oc.get(h,0),"draw_odds":oc.get("Draw",0),"away_odds":oc.get(a,0)}
    return out

# ── Budget ──
def _save_budget(rem):
    DATA_DIR.mkdir(exist_ok=True)
    b = _load_budget(); today = datetime.now().strftime("%Y-%m-%d")
    if b.get("date") != today: b["date"]=today; b["used"]=0
    b["remaining"]=rem; b["used"]=b.get("used",0)+1
    with open(BUDGET_FILE,"w") as f: json.dump(b,f)
def _load_budget():
    if BUDGET_FILE.exists():
        with open(BUDGET_FILE) as f: return json.load(f)
    return {"remaining":"?","used":0,"date":""}

# ── Snapshots & CLV ──
def load_snap():
    if SNAPSHOTS_FILE.exists():
        with open(SNAPSHOTS_FILE) as f: return json.load(f)
    return {}
def save_snap(s):
    DATA_DIR.mkdir(exist_ok=True)
    now=time.time(); cl={k:v for k,v in s.items() if v.get("ts",0)>now-48*3600}
    with open(SNAPSHOTS_FILE,"w") as f: json.dump(cl,f,indent=2)

def record(key, odds, snap):
    if key not in snap:
        ts=0
        try: ts=datetime.fromisoformat(odds["kickoff"].replace("Z","+00:00")).timestamp()
        except: pass
        snap[key]={"home":odds["home"],"away":odds["away"],"kickoff":odds["kickoff"],"ts":ts,
            "op_h":odds["home_odds"],"op_d":odds["draw_odds"],"op_a":odds["away_odds"],"bm":odds.get("bm","")}
    return snap

def check_clv(key, cur, snap):
    s = snap.get(key)
    if not s: return None
    hrs = (s.get("ts",0)-time.time())/3600
    if hrs<-0.5 or hrs>HOURS_WINDOW: return None
    for sk,cfg in STRATEGIES.items():
        op = s["op_a"] if sk=="away_win" else s["op_h"]
        cr = cur["away_odds"] if sk=="away_win" else cur["home_odds"]
        if op<=0 or cr<=0: continue
        clv = op-cr
        if clv<cfg["clv_min"]: continue
        for rng in cfg["ranges"]:
            if rng["min"]<=op<=rng["max"]:
                imp=1/op; prob=min(imp+clv*0.3,0.75)
                edge=prob*op-1; kelly=max(0,min((edge/(op-1))*KELLY_FRAC,0.10)) if edge>0 else 0
                team = s["away"] if sk=="away_win" else s["home"]
                return {"key":key,"strat":sk,"emoji":cfg["emoji"],"name":cfg["name"],
                    "home":s["home"],"away":s["away"],"pick":f"{team} ({'away' if sk=='away_win' else 'home'})",
                    "op":round(op,2),"cur":round(cr,2),"clv":round(clv,3),"kickoff":s["kickoff"],
                    "hrs":round(hrs,1),"kelly":round(kelly*100,1),"wr":rng["hist_wr"],"roi":rng["hist_roi"]}
    return None

def log_sig(s):
    DATA_DIR.mkdir(exist_ok=True); ex=LOG_FILE.exists()
    with open(LOG_FILE,"a",newline="") as f:
        w=csv.DictWriter(f,["ts","home","away","strat","pick","op","cur","clv","kelly","kickoff","result","pnl"])
        if not ex: w.writeheader()
        w.writerow({"ts":datetime.now().isoformat(),"home":s["home"],"away":s["away"],"strat":s["strat"],
            "pick":s["pick"],"op":s["op"],"cur":s["cur"],"clv":s["clv"],"kelly":s["kelly"],"kickoff":s["kickoff"],"result":"","pnl":""})

# ── Main Scan ──
def scan(verbose=True):
    if verbose: print_hdr()
    if verbose: print("  📡 Fetching Flashscore schedule (free)...")
    sched = fetch_schedule()
    leagues = set(m["league"] for m in sched)
    if verbose: print(f"     {len(sched)} matches, {len(leagues)} leagues")
    soon = [m for m in sched if m["hours_until"]<=HOURS_WINDOW]
    if verbose: print(f"  ⏰ {len(soon)} matches within {HOURS_WINDOW}h")
    need = set(match_to_api(m["league"]) for m in soon) - {""}
    if verbose:
        b=_load_budget(); print(f"  🎯 {len(need)} leagues to fetch | Budget: {b.get('remaining','?')} left")
    snap = load_snap(); sigs = []
    if not need:
        if verbose:
            nxt=[m for m in sched if match_to_api(m["league"]) and m["hours_until"]<=12]
            nxt.sort(key=lambda x:x["hours_until"])
            if nxt:
                print(f"\n  📅 Next covered matches:")
                for m in nxt[:8]: print(f"     {m['hours_until']:.1f}h | {m['league']}: {m['home']} vs {m['away']}")
        save_snap(snap); return sigs
    if verbose: print(f"\n  🔍 Fetching odds...")
    for lk in need:
        ln=ODDS_API_LEAGUES.get(lk,lk)
        evts=fetch_odds(lk)
        if not evts: continue
        od=parse_odds(evts)
        if verbose: print(f"     {ln}: {len(od)} matches")
        for k,o in od.items():
            snap=record(k,o,snap)
            sig=check_clv(k,o,snap)
            if sig: sig["league"]=ln; sigs.append(sig)
    save_snap(snap)
    if verbose: print_sigs(sigs); print_status(snap,sched)
    for s in sigs: log_sig(s)
    return sigs

def show_schedule():
    print_hdr(); print("  📅 SCHEDULE (free from Flashscore)\n")
    sched=fetch_schedule(); today=[m for m in sched if m["hours_until"]<=24]
    today.sort(key=lambda x:x["kickoff_ts"])
    by_l={}
    for m in today: by_l.setdefault(m["league"],[]).append(m)
    cov=ncov=0
    for l in sorted(by_l):
        ms=by_l[l]; api=match_to_api(l); mk="✅" if api else "  "
        if api: cov+=len(ms)
        else: ncov+=len(ms)
        print(f"  {mk} {l} ({len(ms)})")
        for m in ms:
            h=int(m["hours_until"]); mn=int((m["hours_until"]-h)*60)
            print(f"      {h}h{mn:02d}m | {m['home']} vs {m['away']}")
    print(f"\n  Total: {len(today)} | ✅ Covered: {cov} | No odds: {ncov}")

def backtest(path):
    import pandas as pd
    print(f"\n  Loading {path}..."); df=pd.read_csv(path); print(f"  {len(df)} matches")
    df["hg"]=df["счет матча"].str.split("-").str[0].astype(float,errors="ignore")
    df["ag"]=df["счет матча"].str.split("-").str[1].astype(float,errors="ignore")
    df["hw"]=df["hg"]>df["ag"]; df["aw"]=df["ag"]>df["hg"]
    df["clv_h"]=df["П1"]-df["П1 ЗАКР"]; df["clv_a"]=df["П2"]-df["П2 ЗАКР"]
    print(f"\n{'='*60}\n  BACKTEST\n{'='*60}")
    for sk,cfg in STRATEGIES.items():
        cc="clv_a" if sk=="away_win" else "clv_h"
        oc="П2" if sk=="away_win" else "П1"
        rc="aw" if sk=="away_win" else "hw"
        print(f"\n  {cfg['emoji']} {cfg['name']}:")
        for rng in cfg["ranges"]:
            m=(df[cc]>=cfg["clv_min"])&(df[oc]>=rng["min"])&(df[oc]<=rng["max"])
            s=df[m].dropna(subset=[rc])
            if len(s)==0: continue
            wr=s[rc].mean(); roi=(s[rc]*s[oc]-1).mean()*100; pnl=(s[rc]*s[oc]-1).sum()
            print(f"    {rng['min']}-{rng['max']}: {len(s)} bets, WR={wr:.1%}, ROI={roi:+.1f}%, P/L={pnl:+.0f}u, ~{len(s)/len(df)*40:.1f}/day")

def print_hdr():
    print(f"\n{'='*60}\n  ⚽ CLV MONITOR v2.0 — Free Tier\n  Flashscore (224 leagues) + the-odds-api (50 leagues)\n{'='*60}")
def print_sigs(sigs):
    if not sigs: print("\n  😴 No signals."); return
    print(f"\n  🚨 {len(sigs)} SIGNAL(S):\n")
    for i,s in enumerate(sigs,1):
        print(f"  {'─'*50}")
        print(f"  #{i} {s['emoji']} {s['name']} | {s.get('league','')}")
        print(f"  {s['home']} vs {s['away']}")
        print(f"  👉 {s['pick']} | Open: {s['op']} → Now: {s['cur']} | CLV: {s['clv']:+.2f}")
        print(f"  ⏰ {s['hrs']}h to kick | 💰 Kelly: {s['kelly']}% | 📈 WR={s['wr']:.0%} ROI=+{s['roi']:.0f}%")
    print(f"  {'─'*50}\n")
def print_status(snap,sched=None):
    now=time.time(); act=sum(1 for s in snap.values() if s.get("ts",0)>now)
    b=_load_budget(); print(f"  📋 Tracking: {act} | Budget: {b.get('remaining','?')} req | Used today: {b.get('used',0)}")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--scan",action="store_true")
    p.add_argument("--schedule",action="store_true"); p.add_argument("--budget",action="store_true")
    p.add_argument("--backtest",action="store_true"); p.add_argument("--backtest-file",default="data/bet365_historical.csv")
    a=p.parse_args(); DATA_DIR.mkdir(exist_ok=True)
    if a.schedule: show_schedule()
    elif a.budget: print_hdr(); b=_load_budget(); print(f"\n  💰 Remaining: {b.get('remaining','?')} | Today: {b.get('used',0)} | Keys: {len(ODDS_API_KEYS)}\n")
    elif a.backtest: print_hdr(); backtest(a.backtest_file)
    elif a.scan: scan()
    else:
        print_hdr(); print(f"\n  🔄 Continuous (every {SCAN_INTERVAL}m). Ctrl+C to stop.\n")
        while True:
            try:
                t=datetime.now().strftime("%H:%M"); sigs=scan(verbose=False)
                if sigs: print(f"  [{t}] 🚨 {len(sigs)} SIGNAL(S)!"); print_sigs(sigs)
                else: b=_load_budget(); print(f"  [{t}] No signals. Budget: {b.get('remaining','?')}")
                time.sleep(SCAN_INTERVAL*60)
            except KeyboardInterrupt: print("\n  ✋ Stopped."); break
            except Exception as e: print(f"  ❌ {e}"); time.sleep(60)

if __name__=="__main__": main()
