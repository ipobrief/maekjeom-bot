# -*- coding: utf-8 -*-
"""맥점 전략 신호 생성: 상위TF 방향필터 + 15분 진입 트리거."""
import numpy as np
import pandas as pd
import indicators as ind


def tf_bias(df: pd.DataFrame) -> pd.Series:
    """단일 타임프레임의 추세 점수.
    일목 구름+전환기준선(-1~+1) + 오실레이터 방향(-1~+1) 합산 → 총 -2~+2."""
    ich = ind.ichimoku(df)
    c = df["close"]

    # ── 일목 구름 + 전환/기준선 (-1 ~ +1) ──
    above_cloud = c > ich["cloud_top"]
    below_cloud = c < ich["cloud_bot"]
    tk_up = ich["tenkan"] > ich["kijun"]
    ichi_score = pd.Series(0.0, index=df.index)
    ichi_score[above_cloud & tk_up] = 1.0
    ichi_score[below_cloud & ~tk_up] = -1.0
    inside = ~above_cloud & ~below_cloud
    ichi_score[inside & tk_up] = 0.5
    ichi_score[inside & ~tk_up] = -0.5

    # ── 오실레이터 방향 (-1 ~ +1) ──
    macd_line, _, _ = ind.macd(c)
    k, _ = ind.stochastic(df)
    rci = ind.rci(c, 26)

    macd_up = macd_line > macd_line.shift(1)
    k_up = k > k.shift(1)
    rci_up = rci > rci.shift(1)

    osc_score = pd.Series(0.0, index=df.index)

    # MACD: 방향 + 0선 위치
    osc_score += macd_up.map({True: 0.4, False: -0.4})
    osc_score += (macd_line > 0).map({True: 0.2, False: -0.2})  # 0선 위치 보정

    # 스토캐스틱: 방향 + 50선 위치
    osc_score += k_up.map({True: 0.2, False: -0.2})
    osc_score += (k > 50).map({True: 0.1, False: -0.1})

    # RCI: 방향 + 0선 위치
    osc_score += rci_up.map({True: 0.2, False: -0.2})
    osc_score += (rci > 0).map({True: 0.1, False: -0.1})

    # 오실레이터 합산 클램프 (-1 ~ +1)
    osc_score = osc_score.clip(-1.0, 1.0)

    return ichi_score + osc_score


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
    rci_long = ind.rci(d["close"], cfg["rci_long"])             # RCI 장기(26·초록) — 트렌드 참고용
    rci_s = ind.rci(d["close"], cfg.get("rci_short", 9))        # RCI 단기(9·파랑)
    rci_m = ind.rci(d["close"], cfg.get("rci_mid", 13))         # RCI 중기(13·오렌지)
    atr = ind.atr(d, cfg["atr_period"])
    b1 = align_bias(tf_bias(df1h), d.index)
    b4 = align_bias(tf_bias(df4h), d.index)
    bd = align_bias(tf_bias(df1d), d.index)
    bias = b1 * 1.0 + b4 * 1.5 + bd * 2.0

    tenkan, kijun = ich["tenkan"], ich["kijun"]
    # 선행스팬1은 차트 '끝점'(현재시점 (전환선+기준선)/2, shift 없음)과 종가 비교.
    # ich["senkou1"]은 shift(26)된 26봉 전 과거값이라 종가 비교에 부적합(회원님 규칙).
    senkou1 = (tenkan + kijun) / 2
    ma20 = ind.sma(d["close"], 20)
    tp = cfg.get("trend_pivot", 5)
    res_line, res_slope = ind.trendline_series(d, "res", tp, tp, with_slope=True)  # 하락 대각선(고점2점)
    sup_line, sup_slope = ind.trendline_series(d, "sup", tp, tp, with_slope=True)  # 상승 대각선(저점2점)
    cs = cfg["chikou_shift"]
    chikou_above = d["close"] > d["close"].shift(cs)    # 후행스팬 > 26봉전 봉
    chikou_below = d["close"] < d["close"].shift(cs)
    rem_req = cfg.get("rem_req", 3)         # 필수2 외 나머지 6개 중 몇 개

    # ── 오실레이터 타점: MACD=방향 / 스토=진입시점 / RCI=보조 ────────────────
    # MACD = 방향: 골든크로스(>시그널)면 롱, 데드크로스면 숏 (0선 돌파 불필요).
    macd_long  = macd_line > macd_sig
    macd_short = macd_line < macd_sig
    # 스토캐스틱 = 진입시점: 50선 돌파(50~80, %K>%D, 상향). 과열80↑·침체20↓ 제외.
    stoch_long  = (k > 50) & (k < 80) & (k > dd) & (k > k.shift(1))
    stoch_short = (k < 50) & (k > 20) & (k < dd) & (k < k.shift(1))
    # RCI(보조): 단기선(9·파랑)이 중기선(13·오렌지) 골든크로스 + 단기선 0선 위 + 상향 → 롱.
    # 각도 검증: 0선 위라도 단기선이 꺾여 내려오는 중이면 무효(오실레이터 방향 원칙).
    # 장기선(26·초록) 0선 돌파는 '롱 유지'일 뿐 진입엔 늦어서 안 씀.
    rci_long_ok  = (rci_s > rci_m) & (rci_s > 0) & (rci_s > rci_s.shift(1))
    rci_short_ok = (rci_s < rci_m) & (rci_s < 0) & (rci_s < rci_s.shift(1))

    # ── 공통 조건
    LM1 = d["close"] > senkou1                        # [필수] 선행스팬1 위
    LM2 = d["close"] > ma20                            # [필수] 20일선 위
    LR1 = chikou_above
    LR2 = tenkan > kijun
    LR3 = (d["close"] > res_line) & (res_slope < 0)    # 하락저항선(고점↓)만 유효, 그걸 상향돌파
    LR5 = stoch_long                                   # 스토 50위 + GC + 상향
    LR7 = rci_long > 0                                 # RCI 그린(장기26) 0선 위 (7번째 나머지)
    SM1 = d["close"] < senkou1
    SM2 = d["close"] < ma20
    SR1 = chikou_below
    SR2 = tenkan < kijun
    SR3 = (d["close"] < sup_line) & (sup_slope > 0)    # 상승지지선(저점↑)만 유효, 그걸 하향이탈
    SR5 = stoch_short                                  # 스토 50아래 + DC + 하향
    SR7 = rci_long < 0                                 # RCI 그린(장기26) 0선 아래

    # ── 잠정(provisional): MACD·스토·RCI 타점 규칙(위치+방향)
    LR4_p = macd_long
    LR6_p = rci_long_ok
    SR4_p = macd_short
    SR6_p = rci_short_ok
    long_rem_p  = (LR1.astype(int) + LR2.astype(int) + LR3.astype(int)
                   + LR4_p.astype(int) + LR5.astype(int) + LR6_p.astype(int) + LR7.astype(int))
    long_all_p  = (LM1 & LM2 & (long_rem_p  >= rem_req)).fillna(False)
    short_rem_p = (SR1.astype(int) + SR2.astype(int) + SR3.astype(int)
                   + SR4_p.astype(int) + SR5.astype(int) + SR6_p.astype(int) + SR7.astype(int))
    short_all_p = (SM1 & SM2 & (short_rem_p >= rem_req)).fillna(False)

    # ── 확정(confirmed): 잠정과 동일 조건(MACD·스토·RCI 모두 위치+방향)
    LR4 = macd_long
    LR6 = rci_long_ok
    SR4 = macd_short
    SR6 = rci_short_ok
    long_rem  = (LR1.astype(int) + LR2.astype(int) + LR3.astype(int)
                 + LR4.astype(int) + LR5.astype(int) + LR6.astype(int) + LR7.astype(int))
    long_all  = (LM1 & LM2 & (long_rem  >= rem_req)).fillna(False)
    long_entry = long_all & ~long_all.shift(1, fill_value=False)
    short_rem = (SR1.astype(int) + SR2.astype(int) + SR3.astype(int)
                 + SR4.astype(int) + SR5.astype(int) + SR6.astype(int) + SR7.astype(int))
    short_all = (SM1 & SM2 & (short_rem >= rem_req)).fillna(False)
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
    out["kd"] = dd                                   # 스토 %D (라벨 GC/DC 표시용)
    out["k_up"] = (k > k.shift(1)).fillna(False)     # 스토 %K 상향 여부
    out["macd_line"] = macd_line
    out["rci_long"] = rci_long
    out["rci_s"] = rci_s
    out["rci_m"] = rci_m                                     # RCI 중기13 (라벨용)
    out["rci_s_up"] = (rci_s > rci_s.shift(1)).fillna(False) # RCI 단기9 상향 여부
    out["swing_low"] = swing_low
    out["swing_high"] = swing_high
    out["bias_1h"], out["bias_4h"], out["bias_1d"] = b1, b4, bd
    out["long"], out["short"] = long_entry, short_entry
    out["long_all"], out["short_all"] = long_all_p, short_all_p  # 잠정용
    out["long_exit"], out["short_exit"] = long_exit, short_exit
    for name, ser in [("LM1", LM1), ("LM2", LM2), ("LR1", LR1), ("LR2", LR2),
                      ("LR3", LR3), ("LR4", LR4), ("LR5", LR5), ("LR6", LR6), ("LR7", LR7),
                      ("SM1", SM1), ("SM2", SM2), ("SR1", SR1), ("SR2", SR2),
                      ("SR3", SR3), ("SR4", SR4), ("SR5", SR5), ("SR6", SR6), ("SR7", SR7)]:
        out[name] = ser.fillna(False)
    return out


def explain(sig_row, cfg) -> dict:
    """단일 봉의 신호 근거를 사람이 읽을 수 있는 dict로 분해."""
    r = sig_row
    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)
    direction_active = "LONG" if r.get("long_all", r["long"]) else ("SHORT" if r.get("short_all", r["short"]) else None)
    bias = r["bias"]
    bias_txt = ("강한 상승" if bias >= 2 else "약한 상승" if bias > 0 else
                "강한 하락" if bias <= -2 else "약한 하락" if bias < 0 else "중립")
    # 스토 %K 현재 구간 표시 (롱: 진입50~80/과열80↑ / 숏: 진입20~50/침체20↓)
    kv = r["k"]
    # 크로스 상태(GC=%K>%D / DC) + 방향(↑상향/↓꺾임) 표시
    cs = ("GC" if kv > r.get("kd", float("nan")) else "DC") + ("↑" if bool(r.get("k_up", False)) else "↓")
    # 값에 맞춰 문구 자체가 바뀜(50돌파인데 45가 나오는 모순 방지)
    stl = (f"스토 50돌파(🥵과열 {kv:.0f}·{cs})" if kv >= 80 else
           f"스토 50돌파({kv:.0f}·{cs})" if kv > 50 else f"스토 50 아래({kv:.0f}·{cs})")
    sts = (f"스토 50이탈(🥶침체 {kv:.0f}·{cs})" if kv <= 20 else
           f"스토 50이탈({kv:.0f}·{cs})" if kv < 50 else f"스토 50 위({kv:.0f}·{cs})")
    # RCI 라벨: 단기9 vs 중기13 실제 크로스 상태 + 방향 + 0선 위치(값)
    r9v = r.get("rci_s", float("nan")); r13v = r.get("rci_m", float("nan"))
    gv = r["rci_long"]
    rrel = ("단>중(GC" if r9v > r13v else "단<중(DC") + ("↑)" if bool(r.get("rci_s_up", False)) else "↓)")
    rl6 = f"RCI {rrel} & 0선 {'돌파' if r9v > 0 else '아래'}({r9v:.0f})"
    rs6 = f"RCI {rrel} & 0선 {'이탈' if r9v < 0 else '위'}({r9v:.0f})"
    rl7 = f"RCI 그린 0선 {'돌파' if gv > 0 else '아래'}({gv:.0f})"
    rs7 = f"RCI 그린 0선 {'이탈' if gv < 0 else '위'}({gv:.0f})"
    must_long = {
        "[필수] 종가 > 선행스팬1": bool(r["LM1"]),
        "[필수] 20일선 위": bool(r["LM2"]),
    }
    rem_long = {
        "후행스팬 > 26봉전": bool(r["LR1"]),
        "전환선 > 기준선": bool(r["LR2"]),
        "하락 대각선 상향돌파": bool(r["LR3"]),
        "MACD 골든크로스(방향)": bool(r["LR4"]),
        stl: bool(r["LR5"]),
        rl6: bool(r["LR6"]),
        rl7: bool(r["LR7"]),
    }
    must_short = {
        "[필수] 종가 < 선행스팬1": bool(r["SM1"]),
        "[필수] 20일선 아래": bool(r["SM2"]),
    }
    rem_short = {
        "후행스팬 < 26봉전": bool(r["SR1"]),
        "전환선 < 기준선": bool(r["SR2"]),
        "상승 대각선 하향이탈": bool(r["SR3"]),
        "MACD 데드크로스(방향)": bool(r["SR4"]),
        sts: bool(r["SR5"]),
        rs6: bool(r["SR6"]),
        rs7: bool(r["SR7"]),
    }
    checks_long = {**must_long, **rem_long}
    checks_short = {**must_short, **rem_short}
    def tf_txt(s):
        if s >= 1.5: return "강상승 ↗"
        if s >= 0.5: return "상승 ↗"
        if s > 0: return "약상승 ↗"
        if s == 0: return "중립 →"
        if s > -0.5: return "약하락 ↘"
        if s > -1.5: return "하락 ↘"
        return "강하락 ↘"

    return {
        "direction": direction,
        "direction_active": direction_active,
        "close": r["close"], "atr": r["atr"],
        "bias": bias, "bias_txt": bias_txt,
        "tf_1h": tf_txt(r.get("bias_1h", 0)),
        "tf_4h": tf_txt(r.get("bias_4h", 0)),
        "tf_1d": tf_txt(r.get("bias_1d", 0)),
        "k": r["k"], "rci_long": r["rci_long"], "rci_s": r.get("rci_s", float("nan")),
        "senkou1": r["senkou1"],
        "swing_low": r.get("swing_low", float("nan")),
        "swing_high": r.get("swing_high", float("nan")),
        "res_line": r.get("res_line", float("nan")),
        "sup_line": r.get("sup_line", float("nan")),
        "ma20": r.get("ma20", float("nan")),
        "checks_long": checks_long, "checks_short": checks_short,
        "must_long": must_long, "rem_long": rem_long,
        "must_short": must_short, "rem_short": rem_short,
    }
