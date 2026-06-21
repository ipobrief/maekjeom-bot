# -*- coding: utf-8 -*-
"""맥점 전략 백테스트 엔진 (15분봉 주력, ATR 손절 + R배수 익절)."""
import numpy as np
import pandas as pd
import data
import strategy

CFG = {
    # 지표
    "atr_period": 14,
    "stoch_os": 25, "stoch_ob": 75,
    "pivot_left": 3, "pivot_right": 3,
    "level_tol": 0.004,            # 직전고저 근접 허용 0.4%
    # 상위TF 필터 임계 (가중합 범위 약 -4.5 ~ +4.5)
    "bias_long_min": 0.0,          # 롱은 비하락(중립 이상)에서만
    "bias_short_max": 0.0,         # 숏은 비상승(중립 이하)에서만
    # 리스크
    "atr_stop_mult": 1.5,          # 손절 = 진입 ± ATR*mult
    "rr_trend": 2.0,               # 추세순응 익절 R배수
    "rr_counter": 1.5,             # 역추세 익절 R배수
    "risk_per_trade": 0.01,        # 자본 1% 리스크
    "fee": 0.0005,                 # 편도 0.05% (taker)
    "slippage": 0.0003,            # 슬리피지 0.03%
    "start_equity": 10000.0,
    # 운영
    "trend_align_thr": 2.0,        # |bias|>=이 값이면 추세순응으로 간주(2R), 아니면 1.5R
}


def run(sig: pd.DataFrame, cfg: dict):
    equity = cfg["start_equity"]
    trades = []
    pos = None  # 진행중 포지션
    eq_curve = []

    for t, row in sig.iterrows():
        price = row["close"]
        # 1) 포지션 관리 (손절/익절은 봉 고저로 판정, 보수적으로 손절 우선)
        if pos:
            hit_sl = row["low"] <= pos["sl"] if pos["dir"] == 1 else row["high"] >= pos["sl"]
            hit_tp = row["high"] >= pos["tp"] if pos["dir"] == 1 else row["low"] <= pos["tp"]
            exit_price = None
            reason = None
            if hit_sl:
                exit_price, reason = pos["sl"], "SL"
            elif hit_tp:
                exit_price, reason = pos["tp"], "TP"
            if exit_price is not None:
                gross = (exit_price - pos["entry"]) * pos["dir"] * pos["qty"]
                fee = (pos["entry"] + exit_price) * pos["qty"] * (cfg["fee"] + cfg["slippage"])
                pnl = gross - fee
                equity += pnl
                trades.append({**pos, "exit": exit_price, "reason": reason,
                               "pnl": pnl, "exit_time": t, "equity": equity})
                pos = None

        # 2) 신규 진입 (포지션 없을 때만, 동시 양다리 금지)
        if pos is None and (row["long"] or row["short"]) and not np.isnan(row["atr"]):
            direction = 1 if row["long"] else -1
            atr = row["atr"]
            entry = price * (1 + cfg["slippage"] * direction)
            stop_dist = atr * cfg["atr_stop_mult"]
            sl = entry - stop_dist * direction
            aligned = abs(row["bias"]) >= cfg["trend_align_thr"] and np.sign(row["bias"]) == direction
            rr = cfg["rr_trend"] if aligned else cfg["rr_counter"]
            tp = entry + stop_dist * rr * direction
            risk_amt = equity * cfg["risk_per_trade"]
            qty = risk_amt / stop_dist if stop_dist > 0 else 0
            if qty > 0:
                pos = {"dir": direction, "entry": entry, "sl": sl, "tp": tp,
                       "qty": qty, "entry_time": t, "rr": rr, "aligned": aligned}
        eq_curve.append({"time": t, "equity": equity})

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
