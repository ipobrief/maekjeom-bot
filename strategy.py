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
    """15분봉 기준 신호 생성 — 선행스팬1 돌파 추세추종 규칙.
    롱 진입 5조건(AND): 종가>선행스팬1, MACD GC/0선위, 스토%K>50,
                        RCI(long)>0, 후행스팬>26봉전 고가.
    숏은 거울. 청산은 핵심 3조건(선행스팬1·MACD·스토) 반대전환."""
    d = df15.copy()
    ich = ind.ichimoku(d)
    macd_line, macd_sig, macd_hist = ind.macd(d["close"])
    k, dd = ind.stochastic(d)
    rci_long = ind.rci(d["close"], cfg["rci_long"])
    atr = ind.atr(d, cfg["atr_period"])
    b1 = align_bias(tf_bias(df1h), d.index)
    b4 = align_bias(tf_bias(df4h), d.index)
    bd = align_bias(tf_bias(df1d), d.index)
    bias = b1 * 1.0 + b4 * 1.5 + bd * 2.0

    senkou1 = ich["senkou1"]
    cs = cfg["chikou_shift"]
    # 후행스팬(현재종가를 cs봉 뒤에 그림)이 그 위치 봉(종가) 위/아래
    chikou_above = d["close"] > d["close"].shift(cs)
    chikou_below = d["close"] < d["close"].shift(cs)
    # MACD 크로스
    macd_gc = (macd_line > macd_sig) & (macd_line.shift(1) <= macd_sig.shift(1))
    macd_dc = (macd_line < macd_sig) & (macd_line.shift(1) >= macd_sig.shift(1))

    # ── 롱 진입: 선행스팬1 돌파(필수) + 보조확증 N개 이상
    L1 = d["close"] > senkou1                  # 필수
    L2 = macd_gc | (macd_line > 0)
    L3 = k > 50
    L4 = rci_long > 0
    L5 = chikou_above
    req = cfg.get("require_confirms", 4)        # 보조 4개 중 몇 개(기본 4=전부)
    long_confirms = (L2.astype(int) + L3.astype(int) + L4.astype(int) + L5.astype(int))
    long_all = (L1 & (long_confirms >= req)).fillna(False)
    long_entry = long_all & ~long_all.shift(1, fill_value=False)   # 충족 시작봉만

    # ── 숏 진입 (거울)
    S1 = d["close"] < senkou1
    S2 = macd_dc | (macd_line < 0)
    S3 = k < 50
    S4 = rci_long < 0
    S5 = chikou_below
    short_confirms = (S2.astype(int) + S3.astype(int) + S4.astype(int) + S5.astype(int))
    short_all = (S1 & (short_confirms >= req)).fillna(False)
    short_entry = short_all & ~short_all.shift(1, fill_value=False)

    # ── 청산(익절)
    if cfg.get("exit_mode", "strict") == "loose":
        # 느슨: 선행스팬1 이탈 한 조건만 → 추세 끝까지 보유(트레일링 효과)
        long_exit = (d["close"] < senkou1).fillna(False)
        short_exit = (d["close"] > senkou1).fillna(False)
    else:
        # 엄격: 핵심 3조건 반대전환
        long_exit = ((d["close"] < senkou1) & (macd_dc | (macd_line < 0)) & (k < 50)).fillna(False)
        short_exit = ((d["close"] > senkou1) & (macd_gc | (macd_line > 0)) & (k > 50)).fillna(False)

    # 직전저점/고점 (손절용, 미래참조 방지: right봉 뒤로 밀어 확정)
    pl, pr = cfg.get("pivot_left", 3), cfg.get("pivot_right", 3)
    swing_low = ind.recent_level(ind.swing_low(d, pl, pr), pr)
    swing_high = ind.recent_level(ind.swing_high(d, pl, pr), pr)

    out = pd.DataFrame(index=d.index)
    out["close"], out["high"], out["low"] = d["close"], d["high"], d["low"]
    out["atr"] = atr
    out["bias"] = bias
    out["senkou1"] = senkou1
    out["k"] = k
    out["macd_line"] = macd_line
    out["rci_long"] = rci_long
    out["swing_low"] = swing_low
    out["swing_high"] = swing_high
    out["bias_1h"] = b1
    out["bias_4h"] = b4
    out["bias_1d"] = bd
    out["long"] = long_entry
    out["short"] = short_entry
    out["long_exit"] = long_exit
    out["short_exit"] = short_exit
    # 근거 분해용 컴포넌트
    for name, ser in [("L1", L1), ("L2", L2), ("L3", L3), ("L4", L4), ("L5", L5),
                      ("S1", S1), ("S2", S2), ("S3", S3), ("S4", S4), ("S5", S5)]:
        out[name] = ser.fillna(False)
    return out


def explain(sig_row, cfg) -> dict:
    """단일 봉의 신호 근거를 사람이 읽을 수 있는 dict로 분해."""
    r = sig_row
    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)
    bias = r["bias"]
    bias_txt = ("강한 상승" if bias >= 2 else "약한 상승" if bias > 0 else
                "강한 하락" if bias <= -2 else "약한 하락" if bias < 0 else "중립")
    checks_long = {
        "종가 > 선행스팬1(초록선)": bool(r["L1"]),
        "MACD 골든크로스/0선 위": bool(r["L2"]),
        "스토캐스틱 %K > 50": bool(r["L3"]),
        "RCI(long) > 0": bool(r["L4"]),
        "후행스팬 > 26봉전 봉": bool(r["L5"]),
    }
    checks_short = {
        "종가 < 선행스팬1(초록선)": bool(r["S1"]),
        "MACD 데드크로스/0선 아래": bool(r["S2"]),
        "스토캐스틱 %K < 50": bool(r["S3"]),
        "RCI(long) < 0": bool(r["S4"]),
        "후행스팬 < 26봉전 봉": bool(r["S5"]),
    }
    def tf_txt(s):
        if s >= 1: return "상승 ↗"
        if s > 0: return "약상승 ↗"
        if s == 0: return "중립 →"
        if s > -1: return "약하락 ↘"
        return "하락 ↘"

    return {
        "direction": direction,
        "close": r["close"], "atr": r["atr"],
        "bias": bias, "bias_txt": bias_txt,
        "tf_1h": tf_txt(r.get("bias_1h", 0)),
        "tf_4h": tf_txt(r.get("bias_4h", 0)),
        "tf_1d": tf_txt(r.get("bias_1d", 0)),
        "k": r["k"], "rci_long": r["rci_long"],
        "senkou1": r["senkou1"],
        "swing_low": r.get("swing_low", float("nan")),
        "swing_high": r.get("swing_high", float("nan")),
        "checks_long": checks_long, "checks_short": checks_short,
    }
