# -*- coding: utf-8 -*-
"""
트랙2 추세매매 백테스트 — 진입 TF(3/5/10분) × 청산구조(전량러너 / 트레일링) 비교.

MM: 롱+숏, 레버리지 10배, 고정증거금, 초기손절=전저점/전고점(무효시 ATR),
    부분익절(옵션), 트레일링 스탑(옵션), 반대신호 청산, 주말(KST) 스킵.
진입: 막돌파(fresh≥3) + HTF 방향정렬(상위 3개 TF bias 일치).
수수료 반영(taker). 펀딩·슬리피지 제외. 공개 시세만 사용.
"""
import numpy as np
import pandas as pd
import data
import strategy
import indicators as ind

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
    "fresh_bars": 2,
}


def load():
    print("데이터 수집 중(3분 ~60일 + HTF 10·30분·1시간)... 시간 좀 걸림")
    return {
        "3m": data.get_history("BTCUSDT", "3m", bars=30000),  # ~62일
        "10m": data.get_history("BTCUSDT", "10m", bars=9000),
        "30m": data.get_history("BTCUSDT", "30m", bars=3000),
        "1h": data.get_history("BTCUSDT", "1h", bars=1500),
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
    # 3분봉 진입, HTF=10·30분·1시간 (ws_watch_3m 구성)
    sig = build_sig(D["3m"], D["10m"], D["30m"], D["1h"], D["10m"], D["30m"], D["1h"])
    f3l = sig["fresh_long"] >= 3
    f3s = sig["fresh_short"] >= 3
    tmL = sum((sig[f"boss_m0_{i}"] & sig[f"boss_mu_{i}"]).astype(int) for i in (1, 2, 3))
    tmS = sum(((~sig[f"boss_m0_{i}"]) & (~sig[f"boss_mu_{i}"])).astype(int) for i in (1, 2, 3))
    sig["B_long"] = sig["long"] & f3l & (tmL >= 2)
    sig["B_short"] = sig["short"] & f3s & (tmS >= 2)

    n = len(sig); mid = n // 2
    h1, h2 = sig.iloc[:mid], sig.iloc[mid:]
    days = max(1, n * 3 // 1440)
    cnt = int(sig["B_long"].sum() + sig["B_short"].sum())
    print("=" * 100)
    print(f"3분봉 {n}개: {sig.index[0]:%Y-%m-%d} ~ {sig.index[-1]:%Y-%m-%d} (~{days}일)")
    print(f"B 신호 {cnt}개 = 하루 {cnt/days:.1f}개  (참고: 10분봉은 하루 ~0.7개)")
    print("[B. 라이브눌림목(MACD정렬) @ 3분봉]  청산=본절런너0.3%")
    print("=" * 100)
    for pname, s in [("전체", sig), ("전반부", h1), ("후반부", h2)]:
        tr, ec = sim(s, long_col="B_long", short_col="B_short", be_after=0.003)
        report(f"  {pname}", tr, ec)
    print("=" * 100)
    print("※ 펀딩·슬리피지 제외. 참고용. 10분봉 B = +16.3%/PF2.58/승률74%(166일).")
