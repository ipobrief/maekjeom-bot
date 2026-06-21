# -*- coding: utf-8 -*-
"""가설 B 검증: 1년치로 추세장 vs 횡보장을 ADX로 나눠 규칙 성과 비교.
각 거래를 '진입봉의 ADX'로 라벨링하여 구간별 집계."""
import os
import pickle
import numpy as np
import pandas as pd
import data
import indicators as ind
import strategy
import backtest as bt


def load_1y():
    cache = "data_cache_1y.pkl"
    if os.path.exists(cache):
        with open(cache, "rb") as f:
            return pickle.load(f)
    print("1년치 수집 중... (조금 걸립니다)")
    dfs = (
        data.get_history("BTCUSDT", "15m", bars=35000),
        data.get_history("BTCUSDT", "1h", bars=9000),
        data.get_history("BTCUSDT", "4h", bars=3000),
        data.get_history("BTCUSDT", "1d", bars=500),
    )
    with open(cache, "wb") as f:
        pickle.dump(dfs, f)
    return dfs


def summarize(trades, label):
    if trades.empty:
        print(f"  [{label}] 거래 없음"); return
    n = len(trades); wr = (trades.pnl > 0).mean() * 100
    wins = trades[trades.pnl > 0].pnl.sum(); losses = abs(trades[trades.pnl <= 0].pnl.sum())
    pf = wins / losses if losses else float("inf")
    exp = trades.pnl.mean()
    print(f"  [{label:8}] 거래 {n:4d}  승률 {wr:4.1f}%  PF {pf:4.2f}  기대값/거래 {exp:7.1f}  총손익 {trades.pnl.sum():9.0f}")


def main():
    df15, df1h, df4h, df1d = load_1y()
    print(f"15m {len(df15)}봉: {df15.index[0]:%Y-%m-%d} ~ {df15.index[-1]:%Y-%m-%d}")

    cfg = dict(bt.CFG); cfg["fixed_notional"] = 1.0; cfg["use_stop"] = False
    cfg["require_confirms"] = 3        # 가설A에서 미세하게 나았던 값

    sig = strategy.build_signals(df15, df1h, df4h, df1d, cfg)
    adx = ind.adx(df15, 14).reindex(sig.index)

    trades, eq = bt.run(sig, cfg)
    if trades.empty:
        print("거래 없음"); return
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["adx"] = trades["entry_time"].map(adx)

    ret = (eq.equity.iloc[-1] / cfg["start_equity"] - 1) * 100
    print(f"\n전체: 수익률 {ret:+.1f}%")
    summarize(trades, "전체")
    print("\n── ADX 구간별 (진입봉 기준) ──")
    summarize(trades[trades.adx >= 25], "추세 ≥25")
    summarize(trades[(trades.adx >= 20) & (trades.adx < 25)], "전이20-25")
    summarize(trades[trades.adx < 20], "횡보 <20")
    print(f"\n참고: ADX 분포 중앙값 {adx.median():.1f}, "
          f"추세봉비율(≥25) {(adx>=25).mean()*100:.0f}%, 횡보봉비율(<20) {(adx<20).mean()*100:.0f}%")


if __name__ == "__main__":
    main()
