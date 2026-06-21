# -*- coding: utf-8 -*-
"""맥점 전략 신호 생성: 상위TF 방향필터 + 15분 진입 트리거."""
import numpy as np
import pandas as pd
import indicators as ind


def tf_bias(df: pd.DataFrame) -> pd.Series:
    """단일 타임프레임의 추세 점수 (-1 하락 / 0 중립 / +1 상승).
    일목 구름 위치 + 전환선·기준선 정렬로 판정."""
    ich = ind.ichimoku(df)
    c = df["close"]
    above_cloud = c > ich["cloud_top"]
    below_cloud = c < ich["cloud_bot"]
    tk_up = ich["tenkan"] > ich["kijun"]
    score = pd.Series(0, index=df.index, dtype=float)
    score[above_cloud & tk_up] = 1.0
    score[below_cloud & ~tk_up] = -1.0
    # 구름 안(중립)은 전환/기준선 방향만 약하게 반영
    inside = ~above_cloud & ~below_cloud
    score[inside & tk_up] = 0.5
    score[inside & ~tk_up] = -0.5
    return score


def align_bias(higher: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """상위TF 점수를 15분 인덱스에 미래참조 없이 정렬(직전 확정값 ffill)."""
    return higher.reindex(higher.index.union(target_index)).ffill().reindex(target_index)


def build_signals(df15, df1h, df4h, df1d, cfg):
    """15분봉 기준 진입 신호 DataFrame 생성."""
    d = df15.copy()
    ich = ind.ichimoku(d)
    macd_line, macd_sig, macd_hist = ind.macd(d["close"])
    k, dd = ind.stochastic(d)
    atr = ind.atr(d, cfg["atr_period"])

    # 상위 TF 방향 필터 (1h+4h+1d 가중합)
    bias = (
        align_bias(tf_bias(df1h), d.index) * 1.0 +
        align_bias(tf_bias(df4h), d.index) * 1.5 +
        align_bias(tf_bias(df1d), d.index) * 2.0
    )

    # 직전고점/저점 (미래참조 방지: right봉 뒤로 밀어 확정값 사용)
    sh = ind.recent_level(ind.swing_high(d, cfg["pivot_left"], cfg["pivot_right"]), cfg["pivot_right"])
    sl = ind.recent_level(ind.swing_low(d, cfg["pivot_left"], cfg["pivot_right"]), cfg["pivot_right"])

    # 스토캐스틱 반전
    k_prev = k.shift(1)
    stoch_up = (k_prev < cfg["stoch_os"]) & (k > k_prev)       # 과매도 바닥 반등
    stoch_dn = (k_prev > cfg["stoch_ob"]) & (k < k_prev)       # 과매수 천정 꺾임
    # MACD 히스토그램 전환
    hist_up = (macd_hist > macd_hist.shift(1)) & (macd_hist.shift(1) <= macd_hist.shift(2))
    hist_dn = (macd_hist < macd_hist.shift(1)) & (macd_hist.shift(1) >= macd_hist.shift(2))

    above_cloud = d["close"] > ich["cloud_top"]
    below_cloud = d["close"] < ich["cloud_bot"]

    # ── 매수맥점: 과매도 반등 + (구름 위 또는 직전저점 근접 지지) + 상위TF 비하락
    near_support = (d["close"] - sl).abs() / d["close"] < cfg["level_tol"]
    long_sig = stoch_up & (hist_up | above_cloud) & (near_support | above_cloud) & (bias >= cfg["bias_long_min"])

    # ── 매도맥점: 과매수 꺾임 + (구름 아래 또는 직전고점 근접 저항) + 상위TF 비상승
    near_resist = (d["close"] - sh).abs() / d["close"] < cfg["level_tol"]
    short_sig = stoch_dn & (hist_dn | below_cloud) & (near_resist | below_cloud) & (bias <= cfg["bias_short_max"])

    out = pd.DataFrame(index=d.index)
    out["close"] = d["close"]
    out["high"] = d["high"]
    out["low"] = d["low"]
    out["atr"] = atr
    out["bias"] = bias
    out["long"] = long_sig.fillna(False)
    out["short"] = short_sig.fillna(False)
    # ── 근거 분해용 컴포넌트 (알림봇이 사람에게 설명) ──
    out["k"] = k
    out["macd_hist"] = macd_hist
    out["stoch_up"] = stoch_up.fillna(False)
    out["stoch_dn"] = stoch_dn.fillna(False)
    out["hist_up"] = hist_up.fillna(False)
    out["hist_dn"] = hist_dn.fillna(False)
    out["above_cloud"] = above_cloud.fillna(False)
    out["below_cloud"] = below_cloud.fillna(False)
    out["near_support"] = near_support.fillna(False)
    out["near_resist"] = near_resist.fillna(False)
    out["swing_high"] = sh
    out["swing_low"] = sl
    out["cloud_top"] = ich["cloud_top"]
    out["cloud_bot"] = ich["cloud_bot"]
    return out


def explain(sig_row, cfg) -> dict:
    """단일 봉의 신호 근거를 사람이 읽을 수 있는 dict로 분해."""
    r = sig_row
    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)
    bias = r["bias"]
    bias_txt = ("강한 상승" if bias >= 2 else "약한 상승" if bias > 0 else
                "강한 하락" if bias <= -2 else "약한 하락" if bias < 0 else "중립")
    checks_long = {
        "스토 과매도 반등(%K<{} 후 상승)".format(cfg["stoch_os"]): bool(r["stoch_up"]),
        "MACD 히스토그램 상승전환": bool(r["hist_up"]),
        "구름 위(추세지지) 또는 직전저점 근접": bool(r["above_cloud"] or r["near_support"]),
        "상위TF 비하락(bias≥{})".format(cfg["bias_long_min"]): bias >= cfg["bias_long_min"],
    }
    checks_short = {
        "스토 과매수 꺾임(%K>{} 후 하락)".format(cfg["stoch_ob"]): bool(r["stoch_dn"]),
        "MACD 히스토그램 하락전환": bool(r["hist_dn"]),
        "구름 아래(추세저항) 또는 직전고점 근접": bool(r["below_cloud"] or r["near_resist"]),
        "상위TF 비상승(bias≤{})".format(cfg["bias_short_max"]): bias <= cfg["bias_short_max"],
    }
    return {
        "direction": direction,
        "close": r["close"], "atr": r["atr"],
        "bias": bias, "bias_txt": bias_txt,
        "k": r["k"], "macd_hist": r["macd_hist"],
        "cloud_top": r["cloud_top"], "cloud_bot": r["cloud_bot"],
        "swing_high": r["swing_high"], "swing_low": r["swing_low"],
        "checks_long": checks_long, "checks_short": checks_short,
    }
