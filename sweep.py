# -*- coding: utf-8 -*-
"""파라미터 변형 비교 — 과최적화 경계하며 '구조적으로' 합리적인 변형만 테스트."""
import copy
import pandas as pd
import strategy
import backtest as bt

df15, df1h, df4h, df1d = bt.load_data()

variants = {
    "기본(중립이상)":        {},
    "추세순응만(강)":        {"bias_long_min": 2.0, "bias_short_max": -2.0},
    "추세순응+넓은손절":     {"bias_long_min": 2.0, "bias_short_max": -2.0, "atr_stop_mult": 2.0, "rr_trend": 2.5},
    "과매도심화+추세순응":   {"bias_long_min": 2.0, "bias_short_max": -2.0, "stoch_os": 15, "stoch_ob": 85},
    "롱만(추세순응)":        {"bias_long_min": 2.0, "bias_short_max": -99},
}

rows = []
for name, override in variants.items():
    cfg = copy.deepcopy(bt.CFG)
    cfg.update(override)
    sig = strategy.build_signals(df15, df1h, df4h, df1d, cfg)
    trades, eq = bt.run(sig, cfg)
    if trades.empty:
        rows.append({"변형": name, "거래": 0}); continue
    n = len(trades)
    wr = (trades["pnl"] > 0).mean() * 100
    ret = (eq["equity"].iloc[-1] / cfg["start_equity"] - 1) * 100
    wins = trades[trades.pnl > 0].pnl.sum(); losses = abs(trades[trades.pnl <= 0].pnl.sum())
    pf = wins / losses if losses else float("inf")
    peak = eq["equity"].cummax(); mdd = ((eq["equity"] - peak) / peak).min() * 100
    rows.append({"변형": name, "거래": n, "승률%": round(wr, 1),
                 "수익률%": round(ret, 1), "PF": round(pf, 2), "MDD%": round(mdd, 1)})

print(pd.DataFrame(rows).to_string(index=False))
