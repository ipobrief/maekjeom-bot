# -*- coding: utf-8 -*-
"""자금관리(70%) 데모 — 같은 시그널에 베팅 크기/차단기만 바꿔서 결과 비교.
(+) 엣지 토대(1시간봉)에 적용해 자금관리의 효과를 격리해서 본다."""
import copy
import pandas as pd
import strategy
import backtest as bt

df15, df1h, df4h, df1d = __import__("regime").load_1y()


def run(risk, fee=0.0, max_dd=None):
    cfg = dict(bt.CFG)
    cfg.update({"rem_req": 3, "trend_pivot": 5, "stop_mode": "swing", "atr_stop_mult": 2.0,
                "risk_per_trade": risk, "maker_fee": fee, "taker_fee": fee,
                "entry_slip": 0.0, "exit_slip": 0.0, "start_equity": 10000.0})
    if max_dd:
        cfg["max_dd_stop"] = max_dd
    sig = strategy.build_signals(df1h, df4h, df1d, df1d, cfg)
    t, e = bt.run(sig, cfg)
    ret = (e.equity.iloc[-1] / cfg["start_equity"] - 1) * 100
    peak = e.equity.cummax(); mdd = ((e.equity - peak) / peak).min() * 100
    return ret, mdd, e.equity.iloc[-1], len(t)


print("토대: 1시간봉, 수수료0(엣지 격리), 같은 시그널 — 베팅 크기만 변경")
print(f"{'거래당 리스크':>12} | {'수익률':>9} | {'최대낙폭':>8} | {'최종자본':>12}")
print("-" * 52)
for risk in [0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.20]:
    ret, mdd, fin, n = run(risk)
    print(f"{risk*100:>10.1f}% | {ret:>+8.0f}% | {mdd:>7.0f}% | {fin:>12,.0f}")

print("\n=== 최대낙폭 차단기 효과 (리스크 5% 고정) ===")
for mdl in [None, 0.30, 0.20]:
    ret, mdd, fin, n = run(0.05, max_dd=mdl)
    tag = "차단기 없음" if mdl is None else f"DD -{mdl*100:.0f}% 차단"
    print(f"{tag:>12}: 수익률{ret:+.0f}% / 최대낙폭{mdd:.0f}% / 최종{fin:,.0f}")

print("\n=== 현실 수수료(메이커 0.02%)에선? (리스크 2%) ===")
ret, mdd, fin, n = run(0.02, fee=0.0002)
print(f"  수익률{ret:+.0f}% / 최대낙폭{mdd:.0f}%  → 엣지가 약하면 자금관리도 못 살림")
