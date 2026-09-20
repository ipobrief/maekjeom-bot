# -*- coding: utf-8 -*-
"""forward 실적 집계 → JSON 출력 (대시보드용). 테스트넷 계좌 조회.
실행: 서버에서 /etc/forward-runner.env 소싱 후 python3 forward_stats.py"""
import os
import json
import datetime

from binance_futures import BinanceFutures

SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
KST = datetime.timezone(datetime.timedelta(hours=9))
MATCH_WIN_MS = 180000  # 거래로그 청산시각 ↔ income 실현시각 매칭 허용오차(3분)


def _opened_ms(rec):
    """거래로그의 opened(KST ISO) → epoch ms. 없으면 None."""
    o = rec.get("opened")
    if not o:
        return None
    try:
        return int(datetime.datetime.fromisoformat(o).timestamp() * 1000)
    except Exception:
        return None


def main():
    ex = BinanceFutures()
    bal, avail = ex.balance_usdt()
    pos = ex.position(SYMBOL)
    inc = ex.income_history(symbol=SYMBOL, limit=1000)

    # FORWARD_START(ms) 이후 거래만 집계 (예전 수동테스트 제외)
    start_ms = int(os.environ.get("FORWARD_START_MS", "0"))
    rp = [[int(x["time"]), float(x["income"]), False]   # [청산ms, pnl, 소비여부]
          for x in inc if x["incomeType"] == "REALIZED_PNL" and int(x["time"]) >= start_ms]
    rp.sort()
    fees = sum(float(x["income"]) for x in inc if x["incomeType"] == "COMMISSION" and int(x["time"]) >= start_ms)
    fund = sum(float(x["income"]) for x in inc if x["incomeType"] == "FUNDING_FEE" and int(x["time"]) >= start_ms)

    # 거래로그(봇이 남긴 진입·청산시각) 로드 → income과 청산시각으로 매칭해 진입기준 거래단위 구성
    trades = []
    tlog = f"trades_{SYMBOL}.jsonl"
    if os.path.exists(tlog):
        with open(tlog, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                cm = int(rec.get("closed_ms", 0))
                if cm < start_ms:
                    continue
                # 이 거래의 청산시각 근처 income 실현손익 합산(부분체결 여러줄 대응)
                pnl = 0.0
                matched = False
                for row in rp:
                    if not row[2] and abs(row[0] - cm) <= MATCH_WIN_MS:
                        pnl += row[1]
                        row[2] = True
                        matched = True
                om = _opened_ms(rec)
                trades.append({
                    "dir": rec.get("dir"),
                    "open_ms": om,
                    "close_ms": cm,
                    "entry_ms": om if om is not None else cm,  # 진입기준(없으면 청산으로 대체)
                    "pnl": round(pnl, 3),
                    "matched": matched,
                    "meta": rec.get("meta") or {},
                    "reason": rec.get("reason"),
                })

    # 로그에 없는 income(로깅 이전 거래) → 진입시각 불명 → 청산시각으로 대체
    for row in rp:
        if not row[2]:
            trades.append({
                "dir": None, "open_ms": None, "close_ms": row[0],
                "entry_ms": row[0], "pnl": round(row[1], 3), "matched": False,
                "meta": {}, "reason": None,
            })

    trades.sort(key=lambda x: x["close_ms"])
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)

    # 누적 실현손익 곡선(청산순)
    cum = 0.0
    curve = []
    for t in trades:
        cum += t["pnl"]
        curve.append({"t": t["close_ms"], "cum": round(cum, 4)})

    # 시간대별 분석 — ★진입시각 기준★. 변동성창 = KST hour ∈ [22,23,0..9]
    def entry_hour(ms):
        return datetime.datetime.fromtimestamp(ms / 1000, KST).hour

    def bucket(items):
        s = sum(t["pnl"] for t in items)
        w = [t for t in items if t["pnl"] > 0]
        l = [t for t in items if t["pnl"] < 0]
        nn = len(items)
        return {
            "n": nn, "pnl": round(s, 3),
            "winrate": round(len(w) / nn * 100, 1) if nn else 0.0,
            "pf": round(sum(t["pnl"] for t in w) / abs(sum(t["pnl"] for t in l)), 2) if l else (99.9 if w else 0.0),
        }

    vol_hours = set([22, 23] + list(range(0, 10)))
    vol = [t for t in trades if entry_hour(t["entry_ms"]) in vol_hours]
    rng = [t for t in trades if entry_hour(t["entry_ms"]) not in vol_hours]
    by_hour = {}
    for t in trades:
        by_hour.setdefault(entry_hour(t["entry_ms"]), []).append(t["pnl"])
    hourly = [{"h": h, "n": len(v), "pnl": round(sum(v), 3)} for h, v in sorted(by_hour.items())]
    tod = {
        "volatile": bucket(vol),   # 진입 KST 22~10시
        "range": bucket(rng),      # 진입 KST 10~22시
        "hourly": hourly,
    }

    state_file = f"trend_state_{SYMBOL}.json"
    state = json.load(open(state_file, encoding="utf-8")) if os.path.exists(state_file) else None

    # ── A/B: MACD필터 페이퍼봇 vs 무필터 실거래 (AB_START_MS 이후 같은 기간) ──
    ab_start = int(os.environ.get("AB_START_MS", "0"))
    mlog = f"trades_MACD_{SYMBOL}.jsonl"
    m_trades = []
    if os.path.exists(mlog):
        for line in open(mlog, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if int(rec.get("closed_ms", 0)) >= ab_start:
                m_trades.append(rec)

    def stat(pl):
        w = [p for p in pl if p > 0]
        l = [p for p in pl if p < 0]
        return {"n": len(pl), "net": round(sum(pl), 3), "wins": len(w), "losses": len(l),
                "pf": round(sum(w) / abs(sum(l)), 2) if l else (99.9 if w else 0.0),
                "winrate": round(len(w) / len(pl) * 100, 1) if pl else 0.0}

    mstate_f = f"paper_state_MACD_{SYMBOL}.json"
    mstate = json.load(open(mstate_f, encoding="utf-8")) if os.path.exists(mstate_f) else None
    ab = {
        "start_ms": ab_start,
        "unfilt": stat([t["pnl"] for t in trades if t["close_ms"] >= ab_start]),
        "macd": stat([float(t["pnl"]) for t in m_trades]),
        "macd_pos": ({"dir": mstate["dir"], "entry": mstate["entry"]} if mstate else None),
        "macd_recent": [{"dir": t["dir"], "open_ms": _opened_ms(t), "close_ms": int(t.get("closed_ms", 0)),
                         "pnl": round(float(t["pnl"]), 3), "reason": t.get("reason")} for t in m_trades[-12:]],
    }

    out = {
        "mode": "testnet" if ex.testnet else "live",
        "symbol": SYMBOL,
        "leverage": int(os.environ.get("LEVERAGE", 10)),
        "margin_per_trade": float(os.environ.get("MARGIN_PER_TRADE", 100)),
        "be_after": float(os.environ.get("BE_AFTER", 0.003)),
        "balance": round(bal, 2),
        "avail": round(avail, 2),
        "position": {"amt": pos["amt"], "entry": pos["entry"], "unrealized": round(pos["unrealized"], 3)},
        "state": state,
        "n_trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "winrate": round(len(wins) / n * 100, 1) if n else 0.0,
        "realized_pnl": round(sum(pnls), 3),
        "fees": round(fees, 3),
        "funding": round(fund, 3),
        "net_pnl": round(sum(pnls) + fees + fund, 3),
        "pf": round(sum(wins) / abs(sum(losses)), 2) if losses else (99.9 if wins else 0.0),
        "avg_win": round(sum(wins) / len(wins), 3) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 3) if losses else 0.0,
        "recent": [{"dir": t["dir"], "open_ms": t["open_ms"], "close_ms": t["close_ms"],
                    "pnl": t["pnl"], "meta": t["meta"], "reason": t.get("reason")}
                   for t in trades[-15:]],
        "curve": curve,
        "tod": tod,
        "ab": ab,
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
