# -*- coding: utf-8 -*-
"""forward 실적 집계 → JSON 출력 (대시보드용). 테스트넷 계좌 조회.
실행: 서버에서 /etc/forward-runner.env 소싱 후 python3 forward_stats.py"""
import os
import json
import datetime

from binance_futures import BinanceFutures

SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")


def main():
    ex = BinanceFutures()
    bal, avail = ex.balance_usdt()
    pos = ex.position(SYMBOL)
    inc = ex.income_history(symbol=SYMBOL, limit=1000)

    # FORWARD_START(ms) 이후 거래만 집계 (예전 수동테스트 제외)
    start_ms = int(os.environ.get("FORWARD_START_MS", "0"))
    rp = [(int(x["time"]), float(x["income"])) for x in inc
          if x["incomeType"] == "REALIZED_PNL" and int(x["time"]) >= start_ms]
    rp.sort()
    pnls = [p for _, p in rp]
    fees = sum(float(x["income"]) for x in inc if x["incomeType"] == "COMMISSION" and int(x["time"]) >= start_ms)
    fund = sum(float(x["income"]) for x in inc if x["incomeType"] == "FUNDING_FEE" and int(x["time"]) >= start_ms)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)

    # 누적 실현손익 곡선
    cum = 0.0
    curve = []
    for t, p in rp:
        cum += p
        curve.append({"t": t, "cum": round(cum, 4)})

    # 시간대별 분석: KST 밤10~오전10시(변동성/추세) vs 오전10~밤10시(횡보)
    # ms(UTC) → KST 시각(UTC+9). 변동성창 = KST hour ∈ [22,23,0..9]
    def kst_hour(ms):
        return int(((ms // 3600000) + 9) % 24)

    def bucket(pnls_by_hour):
        s = sum(p for _, p in pnls_by_hour)
        w = [p for _, p in pnls_by_hour if p > 0]
        l = [p for _, p in pnls_by_hour if p < 0]
        nn = len(pnls_by_hour)
        return {
            "n": nn, "pnl": round(s, 3),
            "winrate": round(len(w) / nn * 100, 1) if nn else 0.0,
            "pf": round(sum(w) / abs(sum(l)), 2) if l else (99.9 if w else 0.0),
        }

    vol_hours = set([22, 23] + list(range(0, 10)))   # 변동성/추세 시간
    vol = [(t, p) for t, p in rp if kst_hour(t) in vol_hours]
    rng = [(t, p) for t, p in rp if kst_hour(t) not in vol_hours]
    by_hour = {}
    for t, p in rp:
        h = kst_hour(t)
        by_hour.setdefault(h, []).append(p)
    hourly = [{"h": h, "n": len(v), "pnl": round(sum(v), 3)} for h, v in sorted(by_hour.items())]
    tod = {
        "volatile": bucket(vol),   # KST 22~10시
        "range": bucket(rng),      # KST 10~22시
        "hourly": hourly,
    }

    state_file = f"trend_state_{SYMBOL}.json"
    state = json.load(open(state_file, encoding="utf-8")) if os.path.exists(state_file) else None

    out = {
        "mode": "testnet" if ex.testnet else "live",
        "symbol": SYMBOL,
        "leverage": int(os.environ.get("LEVERAGE", 10)),
        "margin_per_trade": float(os.environ.get("MARGIN_PER_TRADE", 100)),
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
        "recent": [{"t": t, "pnl": round(p, 3)} for t, p in rp[-15:]],
        "curve": curve,
        "tod": tod,
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
