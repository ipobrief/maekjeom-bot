# -*- coding: utf-8 -*-
"""맥점 전략 백테스트 엔진 (15분봉 주력, ATR 손절 + R배수 익절)."""
import numpy as np
import pandas as pd
import data
import strategy

CFG = {
    "atr_period": 14,
    "rci_long": 26,                # RCI long 기간 (9/13/26 중 long)
    "chikou_shift": 26,            # 후행스팬 시프트
    "pivot_left": 3, "pivot_right": 3,   # 직전저점/고점(손절) 피벗
    "stop_mode": "swing",          # "swing"(직전저점/고점) 또는 "atr"
    "atr_stop_mult": 2.0,          # stop_mode=atr 일 때
    "risk_per_trade": 0.01,        # 자본 1% 리스크
    # 비대칭 수수료/슬리피지: 진입=지정가(메이커), 청산=시장가(타이커)
    "maker_fee": 0.0002, "taker_fee": 0.0005,
    "entry_slip": 0.0, "exit_slip": 0.0003,
    "start_equity": 10000.0,
}


def run(sig: pd.DataFrame, cfg: dict):
    """진입: 5조건 충족봉. 청산: 반대 신호(long_exit/short_exit) 또는 보호손절."""
    equity = cfg["start_equity"]
    trades = []
    pos = None
    eq_curve = []

    for t, row in sig.iterrows():
        # 1) 포지션 관리: 손절(봉 고저, 시장가) 우선 → 신호청산(종가, 시장가)
        if pos:
            exit_price, reason = None, None
            hit_sl = row["low"] <= pos["sl"] if pos["dir"] == 1 else row["high"] >= pos["sl"]
            sig_exit = row["long_exit"] if pos["dir"] == 1 else row["short_exit"]
            if hit_sl:
                exit_price, reason = pos["sl"], "SL"
            elif sig_exit:
                exit_price, reason = row["close"], "EXIT"
            if exit_price is not None:
                exit_fill = exit_price * (1 - cfg["exit_slip"] * pos["dir"])   # 시장가 슬리피지
                gross = (exit_fill - pos["entry"]) * pos["dir"] * pos["qty"]
                fee = (pos["entry"] * cfg["maker_fee"] + exit_fill * cfg["taker_fee"]) * pos["qty"]
                pnl = gross - fee
                equity += pnl
                trades.append({**pos, "exit": exit_fill, "reason": reason,
                               "pnl": pnl, "exit_time": t, "equity": equity})
                pos = None

        # 2) 신규 진입 (포지션 없을 때만) — 지정가(메이커), 슬리피지≈0
        if pos is None and (row["long"] or row["short"]) and not np.isnan(row["atr"]):
            direction = 1 if row["long"] else -1
            entry = row["close"] * (1 + cfg["entry_slip"] * direction)
            # 손절 = 직전저점(롱)/직전고점(숏), 없거나 역방향이면 ATR 대체
            if cfg.get("stop_mode", "swing") == "swing":
                sl = row["swing_low"] if direction == 1 else row["swing_high"]
                if pd.isna(sl) or (direction == 1 and sl >= entry) or (direction == -1 and sl <= entry):
                    sl = entry - row["atr"] * cfg["atr_stop_mult"] * direction
            else:
                sl = entry - row["atr"] * cfg["atr_stop_mult"] * direction
            stop_dist = abs(entry - sl)
            if cfg.get("fixed_notional"):
                qty = (equity * cfg["fixed_notional"]) / entry
            else:
                qty = (equity * cfg["risk_per_trade"]) / stop_dist if stop_dist > 0 else 0
            if not cfg.get("use_stop", True):
                sl = -1e18 if direction == 1 else 1e18
            if qty > 0:
                pos = {"dir": direction, "entry": entry, "sl": sl,
                       "qty": qty, "entry_time": t}
        eq_curve.append({"time": t, "equity": equity})

    if pos is not None:                       # 미청산 포지션 마지막 종가로 정리
        last = sig.iloc[-1]
        gross = (last["close"] - pos["entry"]) * pos["dir"] * pos["qty"]
        equity += gross
        trades.append({**pos, "exit": last["close"], "reason": "OPEN_END",
                       "pnl": gross, "exit_time": sig.index[-1], "equity": equity})

    return pd.DataFrame(trades), pd.DataFrame(eq_curve).set_index("time")


def report(trades: pd.DataFrame, eq: pd.DataFrame, cfg: dict):
    if trades.empty:
        print("거래 없음 — 신호 조건이 너무 엄격합니다.")
        return
    n = len(trades)
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]
    wr = len(wins) / n * 100
    total = trades["pnl"].sum()
    ret = (eq["equity"].iloc[-1] / cfg["start_equity"] - 1) * 100
    pf = wins["pnl"].sum() / abs(losses["pnl"].sum()) if len(losses) and losses["pnl"].sum() != 0 else float("inf")
    peak = eq["equity"].cummax()
    mdd = ((eq["equity"] - peak) / peak).min() * 100
    longs = trades[trades["dir"] == 1]; shorts = trades[trades["dir"] == -1]

    print("=" * 52)
    print(f"  거래수      : {n}  (롱 {len(longs)} / 숏 {len(shorts)})")
    print(f"  승률        : {wr:.1f}%")
    print(f"  총손익      : {total:,.1f} USDT")
    print(f"  수익률      : {ret:+.1f}%  (시작 {cfg['start_equity']:,.0f})")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  최대낙폭(MDD): {mdd:.1f}%")
    print(f"  평균손익    : {trades['pnl'].mean():,.2f} / 거래")
    if len(longs):
        print(f"  롱 승률     : {(longs['pnl']>0).mean()*100:.1f}%  손익 {longs['pnl'].sum():,.1f}")
    if len(shorts):
        print(f"  숏 승률     : {(shorts['pnl']>0).mean()*100:.1f}%  손익 {shorts['pnl'].sum():,.1f}")
    print("=" * 52)


def load_data():
    import os, pickle
    cache = "data_cache.pkl"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return pickle.load(f)
    print("데이터 수집 중...")
    dfs = (
        data.get_history("BTCUSDT", "15m", bars=8000),
        data.get_history("BTCUSDT", "1h", bars=3000),
        data.get_history("BTCUSDT", "4h", bars=2000),
        data.get_history("BTCUSDT", "1d", bars=1000),
    )
    with open(cache, "wb") as f:
        pickle.dump(dfs, f)
    return dfs


def main():
    df15, df1h, df4h, df1d = load_data()
    print(f"15m {len(df15)}봉: {df15.index[0]:%Y-%m-%d} ~ {df15.index[-1]:%Y-%m-%d}")

    sig = strategy.build_signals(df15, df1h, df4h, df1d, CFG)
    print(f"롱 신호 {int(sig['long'].sum())}개 / 숏 신호 {int(sig['short'].sum())}개")

    trades, eq = run(sig, CFG)
    report(trades, eq, CFG)
    trades.to_csv("backtest_trades.csv", index=False, encoding="utf-8-sig")
    print("거래내역 저장: backtest_trades.csv")


if __name__ == "__main__":
    main()
