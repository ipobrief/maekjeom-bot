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


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ADX: 추세 강도(방향 무관). >25 추세, <20 횡보 통상 기준."""
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -l.diff()
    plus_dm = ((up > down) & (up > 0)) * up
    minus_dm = ((down > up) & (down > 0)) * down
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def macd(s: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(s, fast) - ema(s, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def stochastic(df: pd.DataFrame, k=12, d=5, smooth=5):
    """스토캐스틱 슬로우 — 사용자 키움식 설정 기본값(%K길이 12 / %K스무딩 5 / %D스무딩 5).
    k=%K길이(룩백), smooth=%K스무딩(fastK→slowK), d=%D스무딩(slowK→%D)."""
    ll = df["low"].rolling(k).min()
    hh = df["high"].rolling(k).max()
    fast_k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    k_line = fast_k.rolling(smooth).mean()
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


def ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, senkou_b=52, shift=26):
    """일목균형표.
    현재 봉 '아래에 깔린' 구름/선행스팬은 26봉 전 값이 와 있는 것이므로
    shift된 span_a/span_b (= senkou1/2)를 그대로 현재봉 기준선으로 쓴다.
    (미래참조 없음: shift(+26)은 과거값을 현재로 가져오는 것)"""
    h, l = df["high"], df["low"]
    conv = (h.rolling(tenkan).max() + l.rolling(tenkan).min()) / 2      # 전환선
    base = (h.rolling(kijun).max() + l.rolling(kijun).min()) / 2        # 기준선
    span_a = ((conv + base) / 2).shift(shift)            # 선행스팬1 (현재봉 위치의 초록선)
    span_b = ((h.rolling(senkou_b).max() + l.rolling(senkou_b).min()) / 2).shift(shift)  # 선행스팬2
    return pd.DataFrame({
        "tenkan": conv, "kijun": base,
        "senkou1": span_a, "senkou2": span_b,
        "cloud_top": pd.concat([span_a, span_b], axis=1).max(axis=1),
        "cloud_bot": pd.concat([span_a, span_b], axis=1).min(axis=1),
    }, index=df.index)


def rci(s: pd.Series, n: int) -> pd.Series:
    """RCI(Rank Correlation Index): 시간순위와 가격순위의 스피어만 상관, -100~100.
    +면 상승추세(가격이 시간따라 오름), -면 하락추세."""
    denom = n * (n * n - 1)

    def _rci(x):
        m = len(x)
        date_rank = m - np.arange(m)          # 최신=1 ... 과거=m
        order = np.argsort(-x, kind="mergesort")
        price_rank = np.empty(m)
        price_rank[order] = np.arange(1, m + 1)  # 최고가=1
        d = date_rank - price_rank
        return (1 - 6 * np.sum(d * d) / denom) * 100

    return s.rolling(n).apply(_rci, raw=True)


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


def trendline_series(df: pd.DataFrame, kind: str = "res", left: int = 5, right: int = 5,
                     with_slope: bool = False):
    """봉마다 '직전 주요 스윙 2점'을 이은 대각선 추세선 값을 반환(미래참조 없음).
    kind='res' 고점 2개(하락추세선) / 'sup' 저점 2개(상승추세선).
    피벗은 right봉 뒤에 확정되므로 그 시점 이후부터만 사용.
    with_slope=True면 (선, 봉당 기울기) 튜플 반환 — 상승/하락 방향 검증용."""
    piv = swing_high(df, left, right) if kind == "res" else swing_low(df, left, right)
    vals = piv.values
    positions = np.where(~np.isnan(vals))[0]      # 피벗이 위치한 봉
    confirmed = positions + right                  # 그 피벗이 '확정'되는 봉
    line = np.full(len(df), np.nan)
    slp = np.full(len(df), np.nan)
    known = []
    ci = 0
    for t in range(len(df)):
        while ci < len(positions) and confirmed[ci] <= t:
            known.append((positions[ci], vals[positions[ci]]))
            ci += 1
        if len(known) >= 2:
            (i1, y1), (i2, y2) = known[-2], known[-1]
            if i2 != i1:
                slope = (y2 - y1) / (i2 - i1)
                line[t] = y2 + slope * (t - i2)
                slp[t] = slope
    ls = pd.Series(line, index=df.index)
    if with_slope:
        return ls, pd.Series(slp, index=df.index)
    return ls
