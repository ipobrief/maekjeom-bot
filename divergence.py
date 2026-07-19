# -*- coding: utf-8 -*-
"""다이버전스 감지 (MACD선=파란선 기준) — 맥점 신호와 별개 스트림.

종류(과장 제외, 일반·히든만):
  일반(반전): 하락=가격HH & MACD LH / 상승=가격LL & MACD HL
  히든(지속): 하락=가격LH & MACD HH / 상승=가격HL & MACD LL
가격 스윙 고/저점 2개를 이어 그 시점 MACD선(macd()[0], 파란선) 값과 비교.
두 스윙은 최소 min_gap(기본 10)봉 이상 떨어진 것만 비교(너무 가까운 스윙 제외).
봉 마감 기준, 새로 확정된 것만 1회 발송(dedup은 호출측 sent set).
"""
import numpy as np
import pandas as pd
import requests
from zoneinfo import ZoneInfo
import indicators as ind
import alert_bot as ab   # tg_html 재사용

KST = ZoneInfo("Asia/Seoul")

# 2026-07-19: 사용자 요청으로 다이버전스 발송 중단.
# 이유 = 추세장에서 노이즈 과다. 단순 피벗 규칙은 "진짜 다이버전스"와 "추세 하강 중
# 미세 스윙"을 구분 못 함(예: 고점 64,722→64,724 +2p를 HH로, 하락 MACD선 위 아무 두 점을
# LH로 잡아 오탐). 재개하려면 이 값을 True 로만 바꾸면 됨(코드는 그대로 보존).
ENABLED = False


def _pair_confirmed(piv, n, right, min_gap):
    """확정 피벗 중 마지막(i2)과, i2에서 min_gap봉 이상 떨어진 가장 최근 이전 피벗(i1)."""
    pos = [p for p in np.where(~np.isnan(piv.values))[0] if p <= n - 1 - right]
    if len(pos) < 2:
        return None
    i2 = pos[-1]
    earlier = [p for p in pos if i2 - p >= min_gap]
    return (earlier[-1], i2) if earlier else None


def detect(df, macd_ind, pivot=1, max_age=2, min_gap=10, kinds=("일반", "히든")):
    """마지막 봉 기준 '최근 확정된' 다이버전스 목록 반환.
    macd_ind: MACD선(파란선, macd()[0]) 시리즈.
    pivot: 스윙 확정에 필요한 좌우 봉수(피벗1, 확정 지연 최소화).
    min_gap: 비교하는 두 스윙 사이 최소 봉 간격(2026-07-19 추가, 기본 10 — 너무 가까운 스윙 제외).
    max_age: 2번째 피벗 확정 후 이 봉수 이내만 신규로 인정(에지 트리거)."""
    n = len(df)
    out = []
    sh = ind.swing_high(df, pivot, pivot)
    sl = ind.swing_low(df, pivot, pivot)

    hp = _pair_confirmed(sh, n, pivot, min_gap)
    if hp:
        i1, i2 = hp
        if (n - 1) - (i2 + pivot) <= max_age:                     # 최근 확정
            p1, p2 = float(df["high"].iloc[i1]), float(df["high"].iloc[i2])
            s1, s2 = float(macd_ind.iloc[i1]), float(macd_ind.iloc[i2])
            base = dict(t1=df.index[i1], p1=p1, s1=s1, t2=df.index[i2], p2=p2, s2=s2)
            if "일반" in kinds and p2 > p1 and s2 < s1:
                out.append({"type": "일반", "dir": "하락", **base})
            if "히든" in kinds and p2 < p1 and s2 > s1:
                out.append({"type": "히든", "dir": "하락", **base})

    lp = _pair_confirmed(sl, n, pivot, min_gap)
    if lp:
        i1, i2 = lp
        if (n - 1) - (i2 + pivot) <= max_age:
            p1, p2 = float(df["low"].iloc[i1]), float(df["low"].iloc[i2])
            s1, s2 = float(macd_ind.iloc[i1]), float(macd_ind.iloc[i2])
            base = dict(t1=df.index[i1], p1=p1, s1=s1, t2=df.index[i2], p2=p2, s2=s2)
            if "일반" in kinds and p2 < p1 and s2 > s1:
                out.append({"type": "일반", "dir": "상승", **base})
            if "히든" in kinds and p2 > p1 and s2 < s1:
                out.append({"type": "히든", "dir": "상승", **base})
    return out


def _to_kst(t):
    return t.tz_convert(KST) if t.tzinfo else t.tz_localize("UTC").tz_convert(KST)


def card(d, symbol, tf):
    """다이버전스 알림 카드(HTML)."""
    t1, t2 = _to_kst(d["t1"]), _to_kst(d["t2"])
    dirn, typ = d["dir"], d["type"]
    head_emoji = "🔻" if dirn == "하락" else "🔺"
    meaning = "추세 반전 주의" if typ == "일반" else "추세 지속(눌림/되돌림)"
    lvl = "고점" if dirn == "하락" else "저점"
    parr = "↑" if d["p2"] > d["p1"] else ("↓" if d["p2"] < d["p1"] else "≈")
    sarr = "↑" if d["s2"] > d["s1"] else ("↓" if d["s2"] < d["s1"] else "≈")
    return (
        f"🔀 <b>{dirn} 다이버전스 ({typ})</b> — {symbol} ({tf})\n"
        f"⏱ {t2:%Y-%m-%d %H:%M} KST 확정 (봉 마감)\n"
        f"{head_emoji} <b>{meaning}</b>\n"
        f"━━━━━━━━━━━━━\n"
        f"· 비교구간  ① {t1:%m-%d %H:%M}  →  ② {t2:%m-%d %H:%M}\n"
        f"· 가격 {lvl} {parr}  {d['p1']:,.0f}(①) → {d['p2']:,.0f}(②)\n"
        f"· MACD선(파란선) {sarr}  {d['s1']:.0f}(①) → {d['s2']:.0f}(②)\n"
        f"<i>판독이지 매매권유 아님. 다이버전스는 참고용. 최종 판단은 본인.</i>"
    )


def _send(text, token, chat, thread):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": ab.tg_html(text),
               "parse_mode": "HTML", "message_thread_id": thread}
    try:
        j = requests.post(url, data=payload, timeout=10).json()
        if j.get("ok"):
            return True
        clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        j = requests.post(url, data={"chat_id": chat, "text": clean,
                                     "message_thread_id": thread}, timeout=10).json()
        return bool(j.get("ok"))
    except Exception as e:
        print("❌ 다이버전스 전송 오류:", e)
        return False


def check(df, symbol, tf, token, chat, thread, sent):
    """봉 마감 시 호출: 새 다이버전스 감지→발송(dedup: sent set). thread 없으면 무동작."""
    if not ENABLED:                       # 2026-07-19 발송 중단(상단 주석 참조)
        return
    if not (token and chat and thread):
        return
    macd_line = ind.macd(df["close"])[0]     # 파란선(MACD선) 기준
    for dv in detect(df, macd_line):
        key = (dv["type"], dv["dir"], dv["t2"])
        if key in sent:
            continue
        if _send(card(dv, symbol, tf), token, chat, thread):
            print(f"🔀 다이버전스 발송: {tf} {dv['dir']} {dv['type']} @ {dv['t2']}")
            sent.add(key)
