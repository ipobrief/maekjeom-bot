# -*- coding: utf-8 -*-
"""청산 구조 비교 — 진입은 동일(막돌파, 무필터), 청산만 바꿔서 대박을 살리나 검증.
 · 본절0.3%(현재): +0.3% 유리시 손절→본절 이동
 · 본절1.0%: +1% 유리시 본절 이동(더 늦게)
 · 본절없음(홀드): 초기 전저점/전고점 손절만, 본절 이동 안 함 → 반대 막돌파까지 홀드
1분봉 위에서 손절 정밀 판정(룩어헤드X). 공개시세. 펀딩·슬리피지 제외. 20배.
"""
import os
import numpy as np
import pandas as pd
import data
import strategy

SYMBOL = os.environ.get("BT_SYMBOL", "BTCUSDT")
LEVERAGE = 20
MARGIN = 100.0
BE_BUFFER = 0.0012
TAKER = 0.0005
CFG = {"atr_period": 14, "rci_long": 26, "chikou_shift": 26, "pivot_left": 3,
       "pivot_right": 3, "trend_pivot": 8, "rem_req": 3, "atr_stop_mult": 2.0,
       "limit_offset": 0.0003, "trend_lookback": 100, "fresh_bars": 3}


def _pnl(entry, ex, d, qty):
    return (ex - entry) * d * qty - (entry + ex) * qty * TAKER


def sim1m(d1, gov, be_after):
    """be_after=None → 본절 이동 안 함(초기손절만). 아니면 그 % 유리시 본절."""
    idx = d1.index
    hi, lo, cl = d1["high"].values, d1["low"].values, d1["close"].values
    ml = gov["mak_long"].values; ms = gov["mak_short"].values
    xl = gov["rev_long_exit"].values; xs = gov["rev_short_exit"].values
    sl_ = gov["swing_low"].values; sh_ = gov["swing_high"].values
    atrv = gov["atr"].values; barid = gov["barid"].values
    trades = []
    pos = None
    last_bar = -1
    for i in range(len(idx)):
        newbar = i > 0 and barid[i] != barid[i - 1]
        if pos is not None:
            is_long = pos["d"] == 1
            pos["peak"] = max(pos["peak"], hi[i]); pos["trough"] = min(pos["trough"], lo[i])
            if be_after is None:
                stop = pos["swing"]
            elif is_long:
                stop = pos["entry"] * (1 + BE_BUFFER) if pos["peak"] >= pos["entry"] * (1 + be_after) else pos["swing"]
            else:
                stop = pos["entry"] * (1 - BE_BUFFER) if pos["trough"] <= pos["entry"] * (1 - be_after) else pos["swing"]
            closed = False
            if (lo[i] <= stop) if is_long else (hi[i] >= stop):
                moved = be_after is not None and (pos["peak"] >= pos["entry"] * (1 + be_after) if is_long
                                                  else pos["trough"] <= pos["entry"] * (1 - be_after))
                trades.append({"pnl": _pnl(pos["entry"], stop, pos["d"], pos["qty"]),
                               "reason": "본절" if moved else "손절", "d": pos["d"]})
                pos = None; closed = True
            if not closed and ((xl[i]) if is_long else (xs[i])):
                trades.append({"pnl": _pnl(pos["entry"], cl[i], pos["d"], pos["qty"]),
                               "reason": "반대막돌파", "d": pos["d"]})
                pos = None
        if pos is None and newbar and (ml[i] or ms[i]) and i > last_bar:
            d = 1 if ml[i] else -1
            entry = cl[i]
            swing = sl_[i] if d == 1 else sh_[i]
            if pd.isna(swing) or (d == 1 and swing >= entry) or (d == -1 and swing <= entry):
                swing = entry - atrv[i] * CFG["atr_stop_mult"] * d
            pos = {"d": d, "entry": entry, "qty": (MARGIN * LEVERAGE) / entry,
                   "swing": swing, "peak": entry, "trough": entry}
            last_bar = i
    return pd.DataFrame(trades)


def rep(name, tr):
    if tr.empty:
        print(f"  {name:22} | 거래 없음"); return
    w = tr[tr.pnl > 0].pnl; l = tr[tr.pnl < 0].pnl
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else 99.9
    rc = tr["reason"].value_counts().to_dict()
    print(f"  {name:22} | 거래 {len(tr):3d} | 순손익 {tr.pnl.sum():+8.1f} | PF {pf:4.2f} | "
          f"승률 {len(w)/len(tr)*100:4.0f}% | 최대승 {tr.pnl.max():+6.1f} | 최대손 {tr.pnl.min():+6.1f} | {rc}")


if __name__ == "__main__":
    print(f"수집({SYMBOL}) 10m+HTF+1m...")
    d10 = data.get_history(SYMBOL, "10m", bars=12000)
    d30 = data.get_history(SYMBOL, "30m", bars=4500)
    d1h = data.get_history(SYMBOL, "1h", bars=2200)
    d2h = data.get_history(SYMBOL, "2h", bars=1200)
    d1 = data.get_history(SYMBOL, "1m", bars=120000)
    sig = strategy.build_signals(d10, d30, d1h, d2h, CFG)
    g = pd.DataFrame(index=sig.index)
    g["mak_long"] = (sig["long"] & (sig["fresh_long"] >= 3)).astype(bool)
    g["mak_short"] = (sig["short"] & (sig["fresh_short"] >= 3)).astype(bool)
    g["rev_long_exit"] = (sig["short"] & (sig["fresh_short"] >= 3)).astype(bool)
    g["rev_short_exit"] = (sig["long"] & (sig["fresh_long"] >= 3)).astype(bool)
    g["swing_low"] = sig["swing_low"]; g["swing_high"] = sig["swing_high"]; g["atr"] = sig["atr"]
    g["barid"] = np.arange(len(g))
    gc = g.copy(); gc.index = g.index + pd.Timedelta("10min")
    gov = gc.reindex(gc.index.union(d1.index)).ffill().reindex(d1.index)
    for c in ["mak_long", "mak_short", "rev_long_exit", "rev_short_exit"]:
        gov[c] = gov[c].fillna(False).astype(bool)
    n = len(sig)
    variants = [("본절0.3%(현재)", 0.003), ("본절1.0%", 0.01), ("본절없음(홀드)", None)]
    print("=" * 108)
    print(f"[{SYMBOL}] 청산구조 비교 | 진입=막돌파 무필터 | 1분봉 정밀 | 20배 | ~{n*10//1440}일 | 룩어헤드X")
    print("=" * 108)
    half = d1.index[len(d1) // 2]
    for seg, mask in [("전체", slice(None)), ("전반", d1.index < half), ("후반", d1.index >= half)]:
        dd = d1 if seg == "전체" else d1[mask]
        gg = gov if seg == "전체" else gov[mask]
        print(f"[{seg}]")
        for label, ba in variants:
            rep(label, sim1m(dd, gg, ba))
    print("=" * 108)
    print("※ '본절없음(홀드)'이 최대승↑·순손익↑면 본절이 대박 죽인 것. 최대손이 커지는 대가 감수 가치 있나 판단.")
