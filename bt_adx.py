# -*- coding: utf-8 -*-
"""1분봉 ADX 보조게이트 백테스트 — 라이브(막돌파 진입/청산 + 본절런너 0.3%)와 동일 구조.
비교: 무필터 vs A안(진입시점 1분ADX≥thr) vs B안(막돌파후 1분ADX가 thr 돌파하는 시점에 진입).
ADX는 '마감된 1분봉' 값만 사용 → 룩어헤드 없음. 공개 시세만. 펀딩·슬리피지 제외.
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
BE_AFTER = 0.003
TAKER = 0.0005
WEEKEND_OFF = True
START = 1000.0
CFG = {"atr_period": 14, "rci_long": 26, "chikou_shift": 26, "pivot_left": 3,
       "pivot_right": 3, "trend_pivot": 8, "rem_req": 3, "atr_stop_mult": 2.0,
       "limit_offset": 0.0003, "trend_lookback": 100, "fresh_bars": 3}


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    up, dn = h.diff(), -l.diff()
    plus = ((up > dn) & (up > 0)) * up.clip(lower=0)
    minus = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def align_1m(adx1, base_index, base_tf="10min"):
    """1분 ADX를 각 base봉 '마감시점'에 알 수 있는 마지막 1분값으로 정렬(룩어헤드X)."""
    s = adx1.copy()
    s.index = s.index + pd.Timedelta("1min")     # 1분봉 마감시각
    tgt = base_index + pd.Timedelta(base_tf)      # base봉 마감시각
    a = s.reindex(s.index.union(tgt)).ffill().reindex(tgt)
    a.index = base_index
    return a


def _pnl(entry, exit_px, d, qty):
    return (exit_px - entry) * d * qty - (entry + exit_px) * qty * TAKER


def run(sig, adxe, gate=None, defer=False, defer_max=3):
    """gate=None 무필터 / gate=thr A안(진입봉 ADX≥thr) / defer=True B안(막돌파후 ADX 돌파 대기)."""
    eq = START
    trades = []
    pos = None
    pend = None
    adxv = adxe.values
    for i, (t, r) in enumerate(sig.iterrows()):
        if pos is not None:
            is_long = pos["dir"] == 1
            entry = pos["entry"]
            if is_long:
                stop = entry * (1 + BE_BUFFER) if pos["peak"] >= entry * (1 + BE_AFTER) else pos["swing"]
            else:
                stop = entry * (1 - BE_BUFFER) if pos["trough"] <= entry * (1 - BE_AFTER) else pos["swing"]
            closed = False
            if (r["low"] <= stop) if is_long else (r["high"] >= stop):
                be = pos["peak"] >= entry * (1 + BE_AFTER) if is_long else pos["trough"] <= entry * (1 - BE_AFTER)
                p = _pnl(entry, stop, pos["dir"], pos["qty"]); eq += p
                trades.append({"pnl": p, "reason": "본절" if be else "손절"}); pos = None; closed = True
            if not closed:
                opp = r["mak_short"] if is_long else r["mak_long"]
                if opp:
                    p = _pnl(entry, r["close"], pos["dir"], pos["qty"]); eq += p
                    trades.append({"pnl": p, "reason": "반대막돌파"}); pos = None; closed = True
            if pos is not None:
                pos["peak"] = max(pos["peak"], r["high"])
                pos["trough"] = min(pos["trough"], r["low"])
        # B안 대기중 진입
        if pos is None and pend is not None:
            pend["age"] += 1
            still = r["long_all"] if pend["dir"] == 1 else r["short_all"]
            if pend["age"] > defer_max or not still:
                pend = None
            elif not np.isnan(adxv[i]) and adxv[i] >= pend["thr"]:
                pos = _open(r, pend["dir"]); pend = None
        # 신규 막돌파
        if pos is None and pend is None and not np.isnan(r["atr"]):
            if WEEKEND_OFF and t.tz_convert("Asia/Seoul").weekday() >= 5:
                pass
            else:
                gl, gs = bool(r["mak_long"]), bool(r["mak_short"])
                if gl or gs:
                    d = 1 if gl else -1
                    a = adxv[i]
                    if gate is None:
                        pos = _open(r, d)
                    elif defer:
                        if not np.isnan(a) and a >= gate:
                            pos = _open(r, d)
                        else:
                            pend = {"dir": d, "age": 0, "thr": gate}
                    else:  # A안
                        if not np.isnan(a) and a >= gate:
                            pos = _open(r, d)
    return pd.DataFrame(trades), eq


def _open(r, d):
    entry = r["close"]
    swing = r["swing_low"] if d == 1 else r["swing_high"]
    if pd.isna(swing) or (d == 1 and swing >= entry) or (d == -1 and swing <= entry):
        swing = entry - r["atr"] * CFG["atr_stop_mult"] * d
    return {"dir": d, "entry": entry, "qty": (MARGIN * LEVERAGE) / entry,
            "swing": swing, "peak": entry, "trough": entry}


def rep(name, tr, eq):
    if tr.empty:
        print(f"  {name:24} | 거래 없음"); return
    w = tr[tr.pnl > 0].pnl; l = tr[tr.pnl < 0].pnl
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else 99.9
    ret = (eq / START - 1) * 100
    print(f"  {name:24} | 거래 {len(tr):3d} | 순손익 {tr.pnl.sum():+8.1f} | 수익률 {ret:+7.1f}% | "
          f"PF {pf:4.2f} | 승률 {len(w)/len(tr)*100:4.0f}% | 평균 {tr.pnl.mean():+6.2f}")


if __name__ == "__main__":
    print(f"데이터 수집({SYMBOL}): 10m + HTF + 1m ADX용... 시간 걸림")
    d10 = data.get_history(SYMBOL, "10m", bars=12000)
    d30 = data.get_history(SYMBOL, "30m", bars=4500)
    d1h = data.get_history(SYMBOL, "1h", bars=2200)
    d2h = data.get_history(SYMBOL, "2h", bars=1200)
    d1 = data.get_history(SYMBOL, "1m", bars=120000)
    sig = strategy.build_signals(d10, d30, d1h, d2h, CFG)
    # 막돌파 진입/청산 신호(라이브 forward_runner와 동일)
    sig["mak_long"] = sig["long"] & (sig["fresh_long"] >= 3)
    sig["mak_short"] = sig["short"] & (sig["fresh_short"] >= 3)
    adx1 = adx(d1)
    adxe = align_1m(adx1, sig.index)
    adx10 = adx(d10).reindex(sig.index)   # 참고: 10분 ADX

    n = len(sig)
    mid = n // 2
    print("=" * 96)
    print(f"[{SYMBOL}] 10분 {n}봉(~{n*10//1440}일) | 막돌파 진입·청산 + 본절런너0.3% | 20배 | 룩어헤드X")
    print(f"  진입시점 1분ADX 분포: 중앙값 {adxe.median():.1f} / 25%t {adxe.quantile(.25):.1f} / 75%t {adxe.quantile(.75):.1f}")
    print("=" * 96)
    print("[전체구간]")
    rep("무필터(현재 라이브)", *run(sig, adxe, gate=None))
    for thr in (15, 20, 25, 30):
        rep(f"A: 1분ADX≥{thr}", *run(sig, adxe, gate=thr))
    for thr in (20, 25):
        rep(f"B: 막돌파→ADX≥{thr}", *run(sig, adxe, gate=thr, defer=True, defer_max=3))
    for thr in (20, 25):
        g = (adx10 >= thr)
        s2 = sig.copy(); s2["mak_long"] = s2["mak_long"] & g; s2["mak_short"] = s2["mak_short"] & g
        rep(f"[참고]10분ADX≥{thr}", *run(s2, adxe, gate=None))
    # ── 전반/후반 요행검증: 핵심 변형만 ──
    for seg, ss, aa in [("전반(앞40일)", sig.iloc[:mid], adxe.iloc[:mid]),
                        ("후반(뒤40일)", sig.iloc[mid:], adxe.iloc[mid:])]:
        print(f"[{seg}]")
        rep("무필터", *run(ss, aa, gate=None))
        rep("A: 1분ADX≥25", *run(ss, aa, gate=25))
        rep("A: 1분ADX≥30", *run(ss, aa, gate=30))
    print("=" * 96)
    print("※ 필터가 무필터 대비 순손익·PF 개선 + 전·후반 둘 다 양(+)이어야 진짜. 한쪽만 +면 요행.")
