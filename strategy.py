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
    tenkan, kijun = ich["tenkan"], ich["kijun"]
    ma20 = ind.sma(d["close"], 20)
    tp = cfg.get("trend_pivot", 5)
    res_line = ind.trendline_series(d, "res", tp, tp)   # 하락 대각선(고점2점)
    sup_line = ind.trendline_series(d, "sup", tp, tp)   # 상승 대각선(저점2점)
    macd_gc = (macd_line > macd_sig) & (macd_line.shift(1) <= macd_sig.shift(1))
    macd_dc = (macd_line < macd_sig) & (macd_line.shift(1) >= macd_sig.shift(1))

    core_req = cfg.get("core_req", 4)       # 코어 4개 중 몇 개
    bonus_req = cfg.get("bonus_req", 2)     # 보너스 3개 중 몇 개

    # ── 롱: 코어(구조 전환) 4 + 보너스(모멘텀) 3
    LC1 = d["close"] > senkou1                       # 선행스팬1 위
    LC2 = tenkan > kijun                             # 전환선 > 기준선
    LC3 = d["close"] > ma20                           # 20일선 위
    LC4 = d["close"] > res_line                       # 하락 대각선 상향돌파
    LB1 = macd_gc | (macd_line > 0)                   # MACD GC/0선위
    LB2 = k > 50                                      # 스토 50 위
    LB3 = rci_long > 0                                # RCI 0선 위
    long_core = (LC1.astype(int) + LC2.astype(int) + LC3.astype(int) + LC4.astype(int))
    long_bonus = (LB1.astype(int) + LB2.astype(int) + LB3.astype(int))
    long_all = ((long_core >= core_req) & (long_bonus >= bonus_req)).fillna(False)
    long_entry = long_all & ~long_all.shift(1, fill_value=False)

    # ── 숏: 거울
    SC1 = d["close"] < senkou1
    SC2 = tenkan < kijun
    SC3 = d["close"] < ma20
    SC4 = d["close"] < sup_line                       # 상승 대각선 하향이탈
    SB1 = macd_dc | (macd_line < 0)
    SB2 = k < 50
    SB3 = rci_long < 0
    short_core = (SC1.astype(int) + SC2.astype(int) + SC3.astype(int) + SC4.astype(int))
    short_bonus = (SB1.astype(int) + SB2.astype(int) + SB3.astype(int))
    short_all = ((short_core >= core_req) & (short_bonus >= bonus_req)).fillna(False)
    short_entry = short_all & ~short_all.shift(1, fill_value=False)

    # ── 청산(익절) = 반대 셋업 형성 (매수익절=매도진입)
    long_exit = short_all
    short_exit = long_all

    # 직전저점/고점 (손절용)
    pl, pr = cfg.get("pivot_left", 3), cfg.get("pivot_right", 3)
    swing_low = ind.recent_level(ind.swing_low(d, pl, pr), pr)
    swing_high = ind.recent_level(ind.swing_high(d, pl, pr), pr)

    out = pd.DataFrame(index=d.index)
    out["close"], out["high"], out["low"] = d["close"], d["high"], d["low"]
    out["atr"] = atr
    out["bias"] = bias
    out["senkou1"] = senkou1
    out["res_line"] = res_line
    out["sup_line"] = sup_line
    out["ma20"] = ma20
    out["k"] = k
    out["macd_line"] = macd_line
    out["rci_long"] = rci_long
    out["swing_low"] = swing_low
    out["swing_high"] = swing_high
    out["bias_1h"], out["bias_4h"], out["bias_1d"] = b1, b4, bd
    out["long"], out["short"] = long_entry, short_entry
    out["long_exit"], out["short_exit"] = long_exit, short_exit
    for name, ser in [("LC1", LC1), ("LC2", LC2), ("LC3", LC3), ("LC4", LC4),
                      ("LB1", LB1), ("LB2", LB2), ("LB3", LB3),
                      ("SC1", SC1), ("SC2", SC2), ("SC3", SC3), ("SC4", SC4),
                      ("SB1", SB1), ("SB2", SB2), ("SB3", SB3)]:
        out[name] = ser.fillna(False)
    return out


def explain(sig_row, cfg) -> dict:
    """단일 봉의 신호 근거를 사람이 읽을 수 있는 dict로 분해."""
    r = sig_row
    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)
    bias = r["bias"]
    bias_txt = ("강한 상승" if bias >= 2 else "약한 상승" if bias > 0 else
                "강한 하락" if bias <= -2 else "약한 하락" if bias < 0 else "중립")
    core_long = {
        "종가 > 선행스팬1(초록선)": bool(r["LC1"]),
        "전환선 > 기준선": bool(r["LC2"]),
        "20일선 위": bool(r["LC3"]),
        "하락 대각선 상향돌파": bool(r["LC4"]),
    }
    bonus_long = {
        "MACD 골든크로스/0선 위": bool(r["LB1"]),
        "스토캐스틱 %K > 50": bool(r["LB2"]),
        "RCI 0선 위": bool(r["LB3"]),
    }
    core_short = {
        "종가 < 선행스팬1(초록선)": bool(r["SC1"]),
        "전환선 < 기준선": bool(r["SC2"]),
        "20일선 아래": bool(r["SC3"]),
        "상승 대각선 하향이탈": bool(r["SC4"]),
    }
    bonus_short = {
        "MACD 데드크로스/0선 아래": bool(r["SB1"]),
        "스토캐스틱 %K < 50": bool(r["SB2"]),
        "RCI 0선 아래": bool(r["SB3"]),
    }
    checks_long = {**core_long, **bonus_long}
    checks_short = {**core_short, **bonus_short}
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
        "res_line": r.get("res_line", float("nan")),
        "sup_line": r.get("sup_line", float("nan")),
        "ma20": r.get("ma20", float("nan")),
        "checks_long": checks_long, "checks_short": checks_short,
        "core_long": core_long, "bonus_long": bonus_long,
        "core_short": core_short, "bonus_short": bonus_short,
    }
