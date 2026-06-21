# -*- coding: utf-8 -*-
"""가설 A 검증: 진입을 '선행스팬1 돌파 + 보조확증 N개'로 완화.
고정 사이즈(엣지 평가), 신호청산만(손절X)."""
import copy
import pandas as pd
import strategy
import backtest as bt

df15, df1h, df4h, df1d = bt.load_data()
base = dict(bt.CFG); base["fixed_notional"] = 1.0; base["use_stop"] = False

variants = {
    "돌파+확증2": {"require_confirms": 2},
    "돌파+확증3": {"require_confirms": 3},
    "돌파+확증4(전부)": {"require_confirms": 4},
}

rows = []
for name, override in variants.items():
    cfg = copy.deepcopy(base); cfg.update(override)
    sig = strategy.build_signals(df15, df1h, df4h, df1d, cfg)
    trades, eq = bt.run(sig, cfg)
    if trades.empty:
        rows.append({"변형": name, "거래": 0}); continue
    n = len(trades); wr = (trades.pnl > 0).mean() * 100
    ret = (eq.equity.iloc[-1] / cfg["start_equity"] - 1) * 100
    wins = trades[trades.pnl > 0].pnl.sum(); losses = abs(trades[trades.pnl <= 0].pnl.sum())
    pf = wins / losses if losses else float("inf")
    aw = trades[trades.pnl > 0].pnl.mean(); al = trades[trades.pnl <= 0].pnl.mean()
    exp = trades.pnl.mean()
    rows.append({"변형": name, "거래": n, "승률%": round(wr, 1), "PF": round(pf, 2),
                 "평균익": round(aw, 0), "평균손": round(al, 0),
                 "기대값/거래": round(exp, 1), "수익률%": round(ret, 1)})

print("가설 A — 진입 완화 (고정사이즈·신호청산만)")
print(pd.DataFrame(rows).to_string(index=False))
