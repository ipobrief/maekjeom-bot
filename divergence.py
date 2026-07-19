# -*- coding: utf-8 -*-
"""다이버전스 감지 (MACD 시그널선 기준) — 맥점 신호와 별개 스트림.

종류(과장 제외, 일반·히든만):
  일반(반전): 하락=가격HH & MACD LH / 상승=가격LL & MACD HL
  히든(지속): 하락=가격LH & MACD HH / 상승=가격HL & MACD LL
가격 스윙 고/저점 2개(피벗3)를 이어 그 시점 MACD 시그널선 값과 비교.
봉 마감 기준, 새로 확정된 것만 1회 발송(dedup은 호출측 sent set).
"""
import numpy as np
import pandas as pd
import requests
from zoneinfo import ZoneInfo
import indicators as ind
import alert_bot as ab   # tg_html 재사용

KST = ZoneInfo("Asia/Seoul")


def _last2_confirmed(piv, n, right):
    """확정된(우측 right봉 지난) 스윙 피벗의 마지막 2개 위치 반환."""
    pos = [p for p in np.where(~np.isnan(piv.values))[0] if p <= n - 1 - right]
    return (pos[-2], pos[-1]) if len(pos) >= 2 else None


def detect(df, macd_sig, pivot=1, max_age=2, kinds=("일반", "히든")):
    """마지막 봉 기준 '최근 확정된' 다이버전스 목록 반환.
    pivot: 스윙 확정에 필요한 좌우 봉수(2026-07-19 3→2→1, 확정 지연 최소화. 사용자: 다이버전스는 드물어 노이즈 적음).
    max_age: 2번째 피벗 확정 후 이 봉수 이내만 신규로 인정(에지 트리거)."""
    n = len(df)
    out = []
    sh = ind.swing_high(df, pivot, pivot)
    sl = ind.swing_low(df, pivot, pivot)

    hp = _last2_confirmed(sh, n, pivot)
    if hp:
        i1, i2 = hp
        if (n - 1) - (i2 + pivot) <= max_age:                     # 최근 확정
            p1, p2 = float(df["high"].iloc[i1]), float(df["high"].iloc[i2])
            s1, s2 = float(macd_sig.iloc[i1]), float(macd_sig.iloc[i2])
            base = dict(t1=df.index[i1], p1=p1, s1=s1, t2=df.index[i2], p2=p2, s2=s2)
            if "일반" in kinds and p2 > p1 and s2 < s1:
                out.append({"type": "일반", "dir": "하락", **base})
            if "히든" in kinds and p2 < p1 and s2 > s1:
                out.append({"type": "히든", "dir": "하락", **base})

    lp = _last2_confirmed(sl, n, pivot)
    if lp:
        i1, i2 = lp
        if (n - 1) - (i2 + pivot) <= max_age:
            p1, p2 = float(df["low"].iloc[i1]), float(df["low"].iloc[i2])
            s1, s2 = float(macd_sig.iloc[i1]), float(macd_sig.iloc[i2])
            base = dict(t1=df.index[i1], p1=p1, s1=s1, t2=df.index[i2], p2=p2, s2=s2)
            if "일반" in kinds and p2 < p1 and s2 > s1:
                out.append({"type": "일반", "dir": "상승", **base})
            if "히든" in kinds and p2 > p1 and s2 < s1:
                out.append({"type": "히든", "dir": "상승", **base})
    return out


def card(d, symbol, tf):
    """다이버전스 알림 카드(HTML)."""
    t2 = d["t2"]
    t2 = t2.tz_convert(KST) if t2.tzinfo else t2.tz_localize("UTC").tz_convert(KST)
    dirn, typ = d["dir"], d["type"]
    head_emoji = "🔻" if dirn == "하락" else "🔺"
    meaning = "추세 반전 주의" if typ == "일반" else "추세 지속(눌림/되돌림)"
    lvl = "고점" if dirn == "하락" else "저점"
    parr = "↑" if d["p2"] > d["p1"] else ("↓" if d["p2"] < d["p1"] else "≈")
    sarr = "↑" if d["s2"] > d["s1"] else ("↓" if d["s2"] < d["s1"] else "≈")
    return (
        f"🔀 <b>{dirn} 다이버전스 ({typ})</b> — {symbol} ({tf})\n"
        f"⏱ {t2:%Y-%m-%d %H:%M} KST 기준 (봉 마감 확정)\n"
        f"{head_emoji} <b>{meaning}</b>\n"
        f"━━━━━━━━━━━━━\n"
        f"· 가격 {lvl} {parr}  {d['p1']:,.0f} → {d['p2']:,.0f}\n"
        f"· MACD 시그널 {sarr}  {d['s1']:.0f} → {d['s2']:.0f}\n"
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
    if not (token and chat and thread):
        return
    macd_sig = ind.macd(df["close"])[1]
    for dv in detect(df, macd_sig):
        key = (dv["type"], dv["dir"], dv["t2"])
        if key in sent:
            continue
        if _send(card(dv, symbol, tf), token, chat, thread):
            print(f"🔀 다이버전스 발송: {tf} {dv['dir']} {dv['type']} @ {dv['t2']}")
            sent.add(key)
