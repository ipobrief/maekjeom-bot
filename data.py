# -*- coding: utf-8 -*-
"""바이낸스 선물 klines 수집. API 키 불필요(공개 시세).
fapi.binance.com — USD-M 선물(BTCUSDT 무기한) 데이터.
Oracle Cloud(도쿄)에서 실행 시 지역 차단 없음.
선행스팬 등 일목 지표는 고가/저가 기반이라 현물/선물 차이가 크므로 선물 데이터 사용."""
import time
import requests
import pandas as pd

BASE = "https://fapi.binance.com"


def _resample_10m_rows(rows):
    """5분봉 kline 리스트를 10분봉으로 합성(:00,:10,:20 정렬). 반환도 kline 리스트 형식."""
    out = []
    # 짝수(:00,:10,:20 시작) 5분봉부터 2개씩 묶음. open_time(ms) 기준 10분 경계.
    i = 0
    n = len(rows)
    # 첫 5분봉이 10분 경계가 아니면 스킵(정렬 맞추기)
    while i < n and (rows[i][0] // 60000) % 10 != 0:
        i += 1
    while i < n:
        a = rows[i]
        b = rows[i + 1] if i + 1 < n else None
        o = float(a[1]); h = float(a[2]); l = float(a[3]); c = float(a[4]); v = float(a[5])
        ct = a[6]
        if b is not None:
            h = max(h, float(b[2])); l = min(l, float(b[3])); c = float(b[4]); v += float(b[5]); ct = b[6]
        out.append([a[0], f"{o}", f"{h}", f"{l}", f"{c}", f"{v}", ct, "0", 0, "0", "0", "0"])
        i += 2
    return out


def fetch_klines(symbol="BTCUSDT", interval="15m", limit=1000, end_time=None):
    if interval == "10m":
        # 바이낸스 10분봉 미지원 → 5분봉 2배 받아 합성.
        raw = fetch_klines(symbol, "5m", min(1000, limit * 2 + 4), end_time)
        return _resample_10m_rows(raw)[-limit:]
    url = f"{BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if end_time:
        params["endTime"] = end_time
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_history(symbol="BTCUSDT", interval="15m", bars=5000):
    """필요한 봉 수만큼 endTime 페이지네이션으로 거슬러 수집."""
    if interval == "10m":
        # 바이낸스 10분봉 미지원 → 5분봉 2개를 :00/:10/:20 경계로 합성.
        df5 = get_history(symbol, "5m", bars * 2 + 20)
        agg = df5.resample("10min", label="left", closed="left", origin="epoch").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        return agg.dropna(subset=["open"]).tail(bars)
    all_rows = []
    end_time = None
    PER = 1000  # 선물 호출당 최대 1000봉
    while len(all_rows) < bars:
        batch = fetch_klines(symbol, interval, PER, end_time)
        if not batch:
            break
        all_rows = batch + all_rows
        end_time = batch[0][0] - 1  # 가장 오래된 봉보다 1ms 이전
        if len(batch) < PER:
            break
        time.sleep(0.25)  # rate limit 예의
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
    df = pd.DataFrame(all_rows, columns=cols)
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.drop_duplicates("open_time").set_index("time").sort_index()
    return df.tail(bars)


if __name__ == "__main__":
    d = get_history("BTCUSDT", "15m", 2000)
    print(d.tail())
    print(f"\n{len(d)}개 봉, {d.index[0]} ~ {d.index[-1]}")
