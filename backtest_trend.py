# -*- coding: utf-8 -*-
"""
트랙2 추세매매 백테스트 — 진입 TF(3/5/10분) × 청산구조(전량러너 / 트레일링) 비교.

MM: 롱+숏, 레버리지 10배, 고정증거금, 초기손절=전저점/전고점(무효시 ATR),
    부분익절(옵션), 트레일링 스탑(옵션), 반대신호 청산, 주말(KST) 스킵.
진입: 막돌파(fresh≥3) + HTF 방향정렬(상위 3개 TF bias 일치).
수수료 반영(taker). 펀딩·슬리피지 제외. 공개 시세만 사용.
"""
import os
import numpy as np
import pandas as pd
import data
import strategy
import indicators as ind

SYMBOL = os.environ.get("BT_SYMBOL", "BTCUSDT")   # BT_SYMBOL=XAUUSDT 로 종목 교체
LEVERAGE = 10
MARGIN_PER_TRADE = 100.0
PARTIAL_ROE = 0.05
PARTIAL_FRAC = 0.5
BE_BUFFER = 0.0012
TAKER_FEE = 0.0005
START_EQUITY = 1000.0
WEEKEND_OFF = True

CFG = {
    "atr_period": 14, "rci_long": 26, "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3, "trend_pivot": 8, "rem_req": 3,
    "atr_stop_mult": 2.0, "limit_offset": 0.0003, "trend_lookback": 100,
    "fresh_bars": 3,   # 10분봉 = ws_watch_10m/forward_runner와 동일(라이브 일치)
}


def load():
    print(f"데이터 수집 중({SYMBOL} 10분 ~6개월 + HTF 30분·1·2시간)... 시간 좀 걸림")
    return {
        "10m": data.get_history(SYMBOL, "10m", bars=12000),   # ~83일 (nwave 계산량 고려)
        "30m": data.get_history(SYMBOL, "30m", bars=4500),
        "1h": data.get_history(SYMBOL, "1h", bars=2200),
        "2h": data.get_history(SYMBOL, "2h", bars=1200),
    }


def build_sig(base, h1, h2, h3, a1, a2, a3):
    """base=진입TF, (h1,h2,h3)=build_signals HTF, (a1,a2,a3)=정렬 확인용 상위TF."""
    sig = strategy.build_signals(base, h1, h2, h3, CFG)
    ba = strategy.align_bias(strategy.tf_bias(a1), sig.index)
    bb = strategy.align_bias(strategy.tf_bias(a2), sig.index)
    bc = strategy.align_bias(strategy.tf_bias(a3), sig.index)
    sig["htf_long"] = (ba > 0) & (bb > 0) & (bc > 0)
    sig["htf_short"] = (ba < 0) & (bb < 0) & (bc < 0)
    return sig


def align_closed(ind_series, htf_interval, base_index, base_interval):
    """미래참조 없는 상위TF 정렬: 각 base봉 '마감시점'까지 '완료된' 상위봉 값만 사용.
    ind_series=상위TF 개장시각 인덱스. 개장+interval(=마감)에야 값 확정 → 마감시각 기준 ffill."""
    s = ind_series.copy()
    s.index = s.index + htf_interval          # 개장시각 → 마감시각으로 이동
    base_close = base_index + base_interval    # base봉 마감시각
    aligned = s.reindex(s.index.union(base_close)).ffill().reindex(base_close)
    aligned.index = base_index                 # 위치 그대로 base 인덱스로
    return aligned


def boss_closed(dfh, htf_interval, base_index):
    """상위TF MACD 정렬(0선위·상향)을 마감봉 기준으로. (m0, mu) 반환."""
    ml, _, _ = ind.macd(dfh["close"])
    bi = pd.Timedelta("10min")
    m0 = align_closed((ml > 0).astype(float), htf_interval, base_index, bi) >= 0.5
    mu = align_closed((ml > ml.shift(1)).astype(float), htf_interval, base_index, bi) >= 0.5
    return m0.fillna(False), mu.fillna(False)


def _pnl(pos, exit_price, qty):
    gross = (exit_price - pos["entry"]) * pos["dir"] * qty
    return gross - (pos["entry"] + exit_price) * qty * TAKER_FEE


def _row(pos, exit_price, qty, reason, t):
    return {"dir": pos["dir"], "entry": pos["entry"], "exit": exit_price, "qty": qty,
            "reason": reason, "pnl": _pnl(pos, exit_price, qty), "exit_time": t}


def sim(sig, long_col="long", short_col="short", partial_frac=0.0, partial_roe=PARTIAL_ROE,
        use_breakeven=True, trail_mult=0.0, be_after=0.0):
    equity = START_EQUITY
    eq_curve, trades, pos = [], [], None
    for t, r in sig.iterrows():
        if pos:
            is_long = pos["dir"] == 1
            entry = pos["entry"]
            if trail_mult > 0:  # 트레일링: 고점(롱)/저점(숏) 추종, 전저점보다 아래로는 안 감
                if is_long:
                    stop = max(pos["swing_sl"], pos["peak"] - trail_mult * pos["atr_entry"])
                else:
                    stop = min(pos["swing_sl"], pos["trough"] + trail_mult * pos["atr_entry"])
            elif be_after > 0:  # be_after % 유리해지면 손절을 본절로(그전엔 전저점/전고점)
                if is_long:
                    stop = entry * (1 + BE_BUFFER) if pos["peak"] >= entry * (1 + be_after) else pos["swing_sl"]
                else:
                    stop = entry * (1 - BE_BUFFER) if pos["trough"] <= entry * (1 - be_after) else pos["swing_sl"]
            elif pos["partialled"] and use_breakeven:
                stop = entry * (1 + BE_BUFFER) if is_long else entry * (1 - BE_BUFFER)
            else:
                stop = pos["swing_sl"]
            closed = False
            if (r["low"] <= stop) if is_long else (r["high"] >= stop):
                rs = "TRAIL" if trail_mult > 0 else ("SL_BE" if pos["partialled"] else "SL")
                trades.append(_row(pos, stop, pos["qty_left"], rs, t))
                equity += _pnl(pos, stop, pos["qty_left"]); pos, closed = None, True
            if not closed and not pos["partialled"] and partial_frac > 0:
                if (r["high"] >= pos["tp"]) if is_long else (r["low"] <= pos["tp"]):
                    part = pos["qty"] * partial_frac
                    equity += _pnl(pos, pos["tp"], part)
                    trades.append(_row(pos, pos["tp"], part, "TP", t))
                    pos["qty_left"] -= part; pos["partialled"] = True
            if not closed and pos is not None:
                if (r["long_exit"] if is_long else r["short_exit"]):
                    trades.append(_row(pos, r["close"], pos["qty_left"], "EXIT", t))
                    equity += _pnl(pos, r["close"], pos["qty_left"]); pos = None
            if pos is not None:  # 고저 갱신(트레일용)
                pos["peak"] = max(pos["peak"], r["high"])
                pos["trough"] = min(pos["trough"], r["low"])
        if pos is None and not np.isnan(r["atr"]):
            if WEEKEND_OFF and t.tz_convert("Asia/Seoul").weekday() >= 5:
                pass
            else:
                gl = bool(r[long_col]); gs = bool(r[short_col])
                if gl or gs:
                    direction = 1 if gl else -1
                    entry = r["close"]
                    swing = r["swing_low"] if gl else r["swing_high"]
                    if pd.isna(swing) or (gl and swing >= entry) or (not gl and swing <= entry):
                        swing = entry - r["atr"] * CFG["atr_stop_mult"] * direction
                    qty = (MARGIN_PER_TRADE * LEVERAGE) / entry
                    tp = entry * (1 + partial_roe / LEVERAGE) if gl else entry * (1 - partial_roe / LEVERAGE)
                    pos = {"dir": direction, "entry": entry, "qty": qty, "qty_left": qty,
                           "swing_sl": swing, "tp": tp, "partialled": False,
                           "peak": entry, "trough": entry, "atr_entry": r["atr"]}
        eq_curve.append(equity)
    return pd.DataFrame(trades), pd.Series(eq_curve, index=sig.index)


def report(name, trades, ec):
    if trades.empty:
        print(f"  {name:30} | 거래 없음"); return
    total = trades["pnl"].sum()
    ret = (ec.iloc[-1] / START_EQUITY - 1) * 100
    peak = ec.cummax(); mdd = ((ec - peak) / peak).min() * 100
    wins = trades[trades["pnl"] > 0]["pnl"]; losses = trades[trades["pnl"] < 0]["pnl"]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else 99.9
    entries = trades["reason"].isin(["SL", "SL_BE", "EXIT", "TRAIL"]).sum()
    wr = len(wins) / len(trades) * 100
    print(f"  {name:30} | 진입 {entries:3d} | 수익률 {ret:+7.1f}% | MDD {mdd:6.1f}% | "
          f"PF {pf:4.2f} | 승률 {wr:4.0f}%")


if __name__ == "__main__":
    D = load()
    # 10분봉 진입, HTF=30분·1·2시간 (ws_watch_10m / forward_runner 구성)
    sig = build_sig(D["10m"], D["30m"], D["1h"], D["2h"], D["30m"], D["1h"], D["2h"])
    f3l = sig["fresh_long"] >= 3
    f3s = sig["fresh_short"] >= 3
    # ★ 미래참조 제거: 상위TF MACD 정렬을 '마감된 봉'만으로 계산 (look-ahead 없음)
    b30 = boss_closed(D["30m"], pd.Timedelta("30min"), sig.index)
    b1h = boss_closed(D["1h"], pd.Timedelta("1h"), sig.index)
    b2h = boss_closed(D["2h"], pd.Timedelta("2h"), sig.index)
    tmL = (b30[0] & b30[1]).astype(int) + (b1h[0] & b1h[1]).astype(int) + (b2h[0] & b2h[1]).astype(int)
    tmS = ((~b30[0]) & (~b30[1])).astype(int) + ((~b1h[0]) & (~b1h[1])).astype(int) + ((~b2h[0]) & (~b2h[1])).astype(int)
    # 구름 두께(base TF, 미래참조 없음): |선행A-선행B| / 종가
    d10 = D["10m"]
    tk = (d10["high"].rolling(9).max() + d10["low"].rolling(9).min()) / 2
    kj = (d10["high"].rolling(26).max() + d10["low"].rolling(26).min()) / 2
    spanA = (tk + kj) / 2
    spanB = (d10["high"].rolling(52).max() + d10["low"].rolling(52).min()) / 2
    thick = ((spanA - spanB).abs() / d10["close"]).reindex(sig.index)

    # ★ 진짜 눌림목 = N파동(저점高↑ + 조정 피보되돌림 + 직전고점 돌파). 전체기간 계산, 확정피벗만(미래참조X)
    print("N파동(진짜눌림목) 계산 중... (전체기간, 좀 걸림)")
    nl, ns = strategy.nwave_flags(d10, L=3, R=3, only_last=len(d10))
    nl = nl.reindex(sig.index).fillna(False); ns = ns.reindex(sig.index).fillna(False)

    def setc(name, L, S):
        sig[name + "_long"] = L; sig[name + "_short"] = S
    setc("nw", nl, ns)                                         # 진짜눌림목(N파동)만
    setc("nwc", nl & (thick >= 0.002), ns & (thick >= 0.002)) # +구름≥0.2%(얇은횡보 제외)
    setc("nwh", nl & (tmL >= 2), ns & (tmS >= 2))             # +정직HTF 추세정렬
    setc("nwhc", nl & (tmL >= 2) & (thick >= 0.002), ns & (tmS >= 2) & (thick >= 0.002))  # +HTF+구름

    n = len(sig); mid = n // 2
    h1, h2 = sig.iloc[:mid], sig.iloc[mid:]
    days = max(1, n * 10 // 1440)
    print("=" * 100)
    print(f"[{SYMBOL}] 10분 {n}봉 ~{days}일 | 진짜눌림목(N파동) | 미래참조없음 | 청산=본절런너0.3%")
    print("=" * 100)
    variants = [("진짜눌림목(N파동)", "nw"), ("+구름≥0.2%", "nwc"),
                ("+정직HTF", "nwh"), ("+HTF+구름", "nwhc")]
    for label, pre in variants:
        cnt = int(sig[pre + "_long"].sum() + sig[pre + "_short"].sum())
        print(f"\n[{label}] 신호 {cnt}개")
        for pname, s in [("전체", sig), ("전반", h1), ("후반", h2)]:
            tr, ec = sim(s, long_col=pre + "_long", short_col=pre + "_short", be_after=0.003)
            report(f"  {pname}", tr, ec)
    print("=" * 100)
    print("※ 펀딩·슬리피지 제외. N파동=저점高+피보되돌림+직전고점돌파(확정피벗만). 참고용.")
