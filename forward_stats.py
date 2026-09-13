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

    rp = [(int(x["time"]), float(x["income"])) for x in inc if x["incomeType"] == "REALIZED_PNL"]
    rp.sort()
    pnls = [p for _, p in rp]
    fees = sum(float(x["income"]) for x in inc if x["incomeType"] == "COMMISSION")
    fund = sum(float(x["income"]) for x in inc if x["incomeType"] == "FUNDING_FEE")
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    n = len(pnls)

    # 누적 실현손익 곡선
    cum = 0.0
    curve = []
    for t, p in rp:
        cum += p
        curve.append({"t": t, "cum": round(cum, 4)})

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
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
