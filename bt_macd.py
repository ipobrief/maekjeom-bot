# -*- coding: utf-8 -*-
"""MACD 0선 방향필터 백테스트 — 진입=막돌파, 청산=반대막돌파 or 초기손절(홀드, 본절없음).
 · 무필터: 막돌파 즉시 진입
 · 즉시필터: 롱은 MACD>0에서만, 숏은 MACD<0에서만 진입(정렬 안 되면 스킵)
 · 0선돌파대기: 막돌파났는데 정렬 안 되면 대기 → MACD가 0선 넘어 정렬되는 봉에 진입(최대 W봉)
10분 MACD(12/26/9) 라인 부호. 마감봉만(룩어헤드X). 20배. 펀딩·슬리피지 제외.
※ 10분봉 시뮬(손절을 봉 고저로 판정→1분보다 약간 낙관적). 필터 상대효과·전후반 안정성이 핵심.
"""
import os
import numpy as np
import pandas as pd
import data
import strategy

SYMBOL = os.environ.get("BT_SYMBOL", "BTCUSDT")
LEVERAGE = 20
MARGIN = 100.0
TAKER = 0.0005
CFG = {"atr_period": 14, "rci_long": 26, "chikou_shift": 26, "pivot_left": 3,
       "pivot_right": 3, "trend_pivot": 8, "rem_req": 3, "atr_stop_mult": 2.0,
       "limit_offset": 0.0003, "trend_lookback": 100, "fresh_bars": 3}


def _pnl(entry, ex, d, qty):
    return (ex - entry) * d * qty - (entry + ex) * qty * TAKER


def run(sig, macd, mode, wait=6):
    """mode: none / filter / defer. macd=10분 MACD라인(0선 정렬용)."""
    eq = MARGIN * 0 + 1000.0
    tr = []
    pos = None
    pend = None
    ml = sig["mak_long"].values; ms = sig["mak_short"].values
    xl = sig["rev_long_exit"].values; xs = sig["rev_short_exit"].values
    la = sig["long_all"].values; sa = sig["short_all"].values
    lo = sig["low"].values; hi = sig["high"].values; cl = sig["close"].values
    swl = sig["swing_low"].values; swh = sig["swing_high"].values; at = sig["atr"].values
    mv = macd.values

    def op(i, d):
        entry = cl[i]
        swing = swl[i] if d == 1 else swh[i]
        if np.isnan(swing) or (d == 1 and swing >= entry) or (d == -1 and swing <= entry):
            swing = entry - at[i] * CFG["atr_stop_mult"] * d
        return {"d": d, "entry": entry, "qty": (MARGIN * LEVERAGE) / entry, "swing": swing}

    for i in range(len(sig)):
        if pos is not None:
            is_long = pos["d"] == 1
            stop = pos["swing"]   # 홀드: 초기손절만
            closed = False
            if (lo[i] <= stop) if is_long else (hi[i] >= stop):
                tr.append(_pnl(pos["entry"], stop, pos["d"], pos["qty"])); pos = None; closed = True
            if not closed and ((xl[i]) if is_long else (xs[i])):
                tr.append(_pnl(pos["entry"], cl[i], pos["d"], pos["qty"])); pos = None
        # 0선돌파 대기 진입
        if pos is None and pend is not None:
            pend["age"] += 1
            still = la[i] if pend["d"] == 1 else sa[i]
            aligned = (mv[i] > 0) if pend["d"] == 1 else (mv[i] < 0)
            if pend["age"] > wait or not still:
                pend = None
            elif aligned:
                pos = op(i, pend["d"]); pend = None
        # 신규 막돌파
        if pos is None and pend is None and not np.isnan(at[i]):
            if ml[i] or ms[i]:
                d = 1 if ml[i] else -1
                aligned = (mv[i] > 0) if d == 1 else (mv[i] < 0)
                if mode == "none":
                    pos = op(i, d)
                elif mode == "filter":
                    if aligned:
                        pos = op(i, d)
                elif mode == "defer":
                    if aligned:
                        pos = op(i, d)
                    else:
                        pend = {"d": d, "age": 0}
    return pd.DataFrame({"pnl": tr})


def rep(name, tr):
    if tr.empty:
        print(f"  {name:24} | 거래 없음"); return
    w = tr[tr.pnl > 0].pnl; l = tr[tr.pnl < 0].pnl
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else 99.9
    print(f"  {name:24} | 거래 {len(tr):3d} | 순손익 {tr.pnl.sum():+8.1f} | PF {pf:4.2f} | "
          f"승률 {len(w)/len(tr)*100:4.0f}% | 최대승 {tr.pnl.max():+6.1f} | 최대손 {tr.pnl.min():+6.1f}")


if __name__ == "__main__":
    print(f"수집({SYMBOL}) 10m+HTF...")
    d10 = data.get_history(SYMBOL, "10m", bars=12000)
    d30 = data.get_history(SYMBOL, "30m", bars=4500)
    d1h = data.get_history(SYMBOL, "1h", bars=2200)
    d2h = data.get_history(SYMBOL, "2h", bars=1200)
    sig = strategy.build_signals(d10, d30, d1h, d2h, CFG)
    sig["mak_long"] = (sig["long"] & (sig["fresh_long"] >= 3)).astype(bool)
    sig["mak_short"] = (sig["short"] & (sig["fresh_short"] >= 3)).astype(bool)
    sig["rev_long_exit"] = (sig["short"] & (sig["fresh_short"] >= 3)).astype(bool)
    sig["rev_short_exit"] = (sig["long"] & (sig["fresh_long"] >= 3)).astype(bool)
    ema12 = d10["close"].ewm(span=12, adjust=False).mean()
    ema26 = d10["close"].ewm(span=26, adjust=False).mean()
    macd = (ema12 - ema26).reindex(sig.index)
    n = len(sig); mid = n // 2
    print("=" * 96)
    print(f"[{SYMBOL}] MACD 0선필터 | 진입=막돌파 청산=홀드 | 20배 | ~{n*10//1440}일 | 룩어헤드X")
    print("=" * 96)
    for seg, s, m in [("전체", sig, macd), ("전반", sig.iloc[:mid], macd.iloc[:mid]),
                      ("후반", sig.iloc[mid:], macd.iloc[mid:])]:
        print(f"[{seg}]")
        rep("무필터", run(s, m, "none"))
        rep("즉시필터(MACD정렬)", run(s, m, "filter"))
        rep("0선돌파대기(최대6봉)", run(s, m, "defer", wait=6))
    print("=" * 96)
    print("※ 필터가 무필터보다 순손익·PF↑ + 전·후반 둘다 개선이어야 유효. 최대승 지키는지도 확인.")
