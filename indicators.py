# -*- coding: utf-8 -*-
"""맥점 전략용 기술적 지표 계산 (pandas 기반)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def macd(s: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stochastic(df: pd.DataFrame, k=14, d=3, smooth=3):
    ll = df["low"].rolling(k).min()
    hh = df["high"].rolling(k).max()
    fast_k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    k_line = fast_k.rolling(smooth).mean()
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


def ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, senkou_b=52, shift=26):
    """일목균형표. 구름은 미래로 shift된 값과 현재 정렬을 위해
    span_a_now / span_b_now (현재 봉 위치에 와 있는 구름)를 함께 반환."""
    h, l = df["high"], df["low"]
    conv = (h.rolling(tenkan).max() + l.rolling(tenkan).min()) / 2      # 전환선
    base = (h.rolling(kijun).max() + l.rolling(kijun).min()) / 2        # 기준선
    span_a = ((conv + base) / 2).shift(shift)                          # 선행스팬1 (미래)
    span_b = ((h.rolling(senkou_b).max() + l.rolling(senkou_b).min()) / 2).shift(shift)  # 선행스팬2
    chikou = df["close"].shift(-shift)                                  # 후행스팬
    # 현재 봉 위치에 도달해 있는 구름 (shift 안 된 원본을 그대로)
    span_a_now = (conv + base) / 2
    span_b_now = (h.rolling(senkou_b).max() + l.rolling(senkou_b).min()) / 2
    return pd.DataFrame({
        "tenkan": conv, "kijun": base,
        "span_a": span_a, "span_b": span_b, "chikou": chikou,
        "cloud_top": pd.concat([span_a_now, span_b_now], axis=1).max(axis=1),
        "cloud_bot": pd.concat([span_a_now, span_b_now], axis=1).min(axis=1),
    }, index=df.index)


def swing_high(df: pd.DataFrame, left=3, right=3) -> pd.Series:
    """직전고점(프랙탈). right봉 확정 후에만 True가 되도록 right만큼 지연 반영은
    백테스트에서 shift로 처리. 여기선 raw fractal 가격을 반환(피벗 위치에 값)."""
    h = df["high"]
    cond = pd.Series(True, index=df.index)
    for i in range(1, left + 1):
        cond &= h > h.shift(i)
    for i in range(1, right + 1):
        cond &= h > h.shift(-i)
    return h.where(cond)


def swing_low(df: pd.DataFrame, left=3, right=3) -> pd.Series:
    l = df["low"]
    cond = pd.Series(True, index=df.index)
    for i in range(1, left + 1):
        cond &= l < l.shift(i)
    for i in range(1, right + 1):
        cond &= l < l.shift(-i)
    return l.where(cond)


def recent_level(pivot: pd.Series, right: int) -> pd.Series:
    """피벗 가격을 right봉 뒤로 밀어(미래참조 방지) 직전 확정 레벨로 forward-fill."""
    return pivot.shift(right).ffill()
