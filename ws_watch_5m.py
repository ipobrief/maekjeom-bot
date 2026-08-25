# -*- coding: utf-8 -*-
"""맥점 웹소켓 실시간 감시 — 5분봉. 맥점방(후행·전환 정렬)/막돌파방 풀 라우팅.
토큰: chris15m_bot(TELEGRAM_TOKEN_1M, →chris5m_bot 개명). 맥점방=TELEGRAM_THREAD_ID_5M, 막돌파방=TELEGRAM_BO_THREAD_5M.
실행: python ws_watch_5m.py  [--no-confirm]
"""
import os
import sys
import json
import asyncio
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import websockets

import data
import strategy
import alert_bot as ab
import divergence

SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")   # env로 종목 교체(XAUUSDT 등)
TF = "5m"
HTF = ("10m", "30m", "1h")
HTF_LABELS = ("10분", "30분", "1시간")
KST = ZoneInfo("Asia/Seoul")

CFG = {
    "atr_period": 14, "rci_long": 26, "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3, "trend_pivot": 8, "rem_req": 3,
    "atr_stop_mult": 2.0, "limit_offset": 0.0003, "trend_lookback": 100,
    "fresh_bars": int(os.environ.get("FRESH_BARS", "2")),   # 막돌파 동시성 창(봉). XAU 상위봉=3, 기본2
}

WS_BASE = os.environ.get("WS_BASE", "wss://stream.binance.com:9443")  # XAU=wss://fstream.binance.com
WS_URL = f"{WS_BASE}/ws/{SYMBOL.lower()}@kline_{TF}"
HTF_REFRESH_SEC = 300
RECOMPUTE_MIN_SEC = 30
PROV_MIN_MINS_LEFT = 1


def kst(ts):
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)


def _token():
    return os.environ.get("TELEGRAM_TOKEN_1M")


def tg_send(text):
    token = _token()
    chat = os.environ.get("TELEGRAM_CHAT_ID_1M")
    if not token or not chat:
        print("⚠️ 5m봇 텔레그램 미설정 (콘솔만).")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": ab.tg_html(text), "parse_mode": "HTML"}
    thread = os.environ.get("TELEGRAM_THREAD_ID_5M")   # 맥점방 30분봉 토픽
    if thread:
        payload["message_thread_id"] = thread
    try:
        j = requests.post(url, data=payload, timeout=10).json()
        if j.get("ok"):
            return True
        clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        pl = {"chat_id": chat, "text": clean}
        if thread:
            pl["message_thread_id"] = thread
        return bool(requests.post(url, data=pl, timeout=10).json().get("ok"))
    except Exception as e:
        print("❌ 텔레그램(30m) 오류:", e)
        return False


def emit(text):
    print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    tg_send(text)


def bo_ready():
    return bool(os.environ.get("TELEGRAM_CHAT_ID_BO") and os.environ.get("TELEGRAM_BO_THREAD_5M"))

def emit_breakout(text):
    print("🎯[막돌파방-5m]", text[:50])
    return ab.tg_send_room(text, _token(), os.environ.get("TELEGRAM_CHAT_ID_BO"),
                           os.environ.get("TELEGRAM_BO_THREAD_5M"))


def pb_ready():
    return bool(os.environ.get("TELEGRAM_THREAD_ID_PULLBACK"))

def emit_pullback(text):
    print("🏹[눌림목방-5m]", text[:50])
    return ab.tg_send_room(text, _token(), os.environ.get("TELEGRAM_CHAT_ID_1M"),
                           os.environ.get("TELEGRAM_THREAD_ID_PULLBACK"))


def fmt_checks(checks):
    return "\n".join(f"  {'✅' if v else '❌'} {k}" for k, v in checks.items())


def fmt_signal(e, when, provisional=False, mins_left=None, active_dir=None):
    d = active_dir if active_dir is not None else e["direction"]
    long_ = d == "LONG"
    side = "🟢 롱(LONG)" if long_ else "🔴 숏(SHORT)"
    px = e["close"]
    swing = e["swing_low"] if long_ else e["swing_high"]
    bad = (swing != swing) or (long_ and swing >= px) or (not long_ and swing <= px)
    if bad:
        swing = px - e["atr"] * CFG["atr_stop_mult"] * (1 if long_ else -1)
        sl_txt = f"{swing:,.1f} (직전저저점 불명확→ATR 대체)"
    else:
        sl_txt = f"{swing:,.1f} ({'직전저점' if long_ else '직전고점'})"
    risk_pct = abs(px - swing) / px * 100
    must = e["must_long"] if long_ else e["must_short"]
    rem = e["rem_long"] if long_ else e["rem_short"]
    badge = ""
    fresh = e.get("fresh_long" if long_ else "fresh_short", 0)
    aligned = fresh >= 3 and ((e.get("r1_long") and e.get("r2_long")) if long_
                              else (e.get("r1_short") and e.get("r2_short")))
    if fresh >= 3:
        if aligned:
            badge += "⭐ <b>맥점 완성</b> — 막돌파 + 후행·전환 정렬\n"
        else:
            badge += f"🎯 <b>막돌파 맥점</b> — 핵심 트리거 동시돌파({fresh}/3, 최근 2봉)\n"
    if (e.get("nwave_long") if long_ else e.get("nwave_short")):
        badge += "🌊 <b>N파동</b> — 조정 후 직전고점 돌파\n"
    rem_n = sum(rem.values()); n_tot = len(rem)
    if rem_n == n_tot:
        badge += f"⭐ <b>전조건 정렬</b>(나머지 {rem_n}/{n_tot}, 그린26 포함) — 추세 진행 중, 추격 주의\n"
    elif rem_n == n_tot - 1:
        badge += f"🔥 <b>강신호</b> — 나머지 {rem_n}/{n_tot}\n"
    dir_line = f"<b>{side} {'예비신호 (잠정)' if provisional else '진입신호'}</b> — {SYMBOL} ({TF})\n"
    top_warn = ("📏 [진입 전 점검]\n"
                "1. 역추세 매매금지! 추세매매(눌림목)만 공략!\n"
                "2. X추세선 돌파 '확인 후' 델타지역 진입 (예측 금지!)\n"
                "3. 반드시 맥점 '초입'에서 진입\n"
                "4. 손절=전고/전저, 익절=X추세선 알람 (손실 짧게·수익 길게)\n")
    if provisional:
        left = f"마감 {mins_left:.0f}분 전" if mins_left is not None else "마감 전"
        head = f"⏱ {kst(when):%Y-%m-%d %H:%M} KST 봉 형성중 · {left}\n"
    else:
        head = f"⏱ {kst(when):%Y-%m-%d %H:%M} KST ({TF} 마감)\n"
    fib_warn = ab.pullback_note(e, long_)   # 추세방향 눌림목 표시(역추세 경고 대체)
    box = "" if aligned else ((("🟩" if long_ else "🟥") + f" 🎯 <b>막돌파 맥점 · {'LONG' if long_ else 'SHORT'} · {fresh}/3</b> " + ("🟩" if long_ else "🟥") + "\n") if fresh >= 3 else "")
    return (
        ab.bold_all(box + dir_line + badge + head).rstrip() + "\n"
        + f"<blockquote>{ab.bold_all(ab.fmt_boss(e, long_, HTF_LABELS) + fib_warn).rstrip()}</blockquote>\n"
        + f"<blockquote><b>{top_warn.rstrip()}</b></blockquote>\n"
        + f"<b>필수 {sum(must.values())}/2</b>\n{fmt_checks(must)}\n"
        + f"<b>나머지 {sum(rem.values())}/{len(rem)} (≥{CFG['rem_req']} 필요)</b>\n{fmt_checks(rem)}\n"
        + f"<i>판독이지 매매권유 아님. 최종 판단은 본인.</i>"
    )


def enrich(row, sig):
    return strategy.explain(row, CFG)


class LiveState:
    def __init__(self):
        self.df0 = None
        self.dfA = self.dfB = self.dfC = None
        self.htf_loaded_at = 0.0
        self.alerted_bar = None
        self.alerted_dirs = set()
        self.last_dir = None
        self.sent_key = None
        self.sent_bo = None
        self.bo_last_dir = None
        self.sent_div = set()
        self.last_recompute = 0.0
        self.maek_last_sent = {}   # 방향별 마지막 맥점 발송 봉시각(같은방향 30분 스로틀)
        self.bo_last_sent = {}     # 방향별 마지막 막돌파 발송 봉시각(같은방향 30분 스로틀)
        self.sent_pb = None        # 눌림목방 같은봉 중복 방지
        self.pb_last_sent = {}     # 방향별 마지막 눌림목 발송 봉시각(같은방향 30분 스로틀)

    def same_dir_blocked(self, d, when):
        return d == self.last_dir

    def maek_ok(self, d, when, minutes=30):
        """맥점방 같은 방향: 마지막 발송 후 minutes분 지났으면 True(재발송 허용)."""
        prev = self.maek_last_sent.get(d)
        return prev is None or (when - prev) >= pd.Timedelta(minutes=minutes)

    def bo_ok(self, d, when, minutes=30):
        """막돌파방 같은 방향: 마지막 발송 후 minutes분 지났으면 True(재발송 허용)."""
        prev = self.bo_last_sent.get(d)
        return prev is None or (when - prev) >= pd.Timedelta(minutes=minutes)

    def pb_ok(self, d, when, minutes=30):
        """눌림목방 같은 방향: 마지막 발송 후 minutes분 지났으면 True(재발송 허용)."""
        prev = self.pb_last_sent.get(d)
        return prev is None or (when - prev) >= pd.Timedelta(minutes=minutes)

    def load_base(self):
        self.df0 = data.get_history(SYMBOL, TF, bars=600)
        self._load_htf()

    def _load_htf(self):
        self.dfA = data.get_history(SYMBOL, HTF[0], bars=400)
        self.dfB = data.get_history(SYMBOL, HTF[1], bars=300)
        self.dfC = data.get_history(SYMBOL, HTF[2], bars=200)
        self.htf_loaded_at = dt.datetime.now().timestamp()

    def maybe_refresh_htf(self):
        if dt.datetime.now().timestamp() - self.htf_loaded_at > HTF_REFRESH_SEC:
            self._load_htf()

    def upsert_bar(self, k):
        t = pd.to_datetime(k["t"], unit="ms", utc=True)
        row = {"open": float(k["o"]), "high": float(k["h"]),
               "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"])}
        self.df0.loc[t] = row
        self.df0 = self.df0[~self.df0.index.duplicated(keep="last")].sort_index().tail(600)
        return t

    def evaluate(self, idx):
        sig = strategy.build_signals(self.df0, self.dfA, self.dfB, self.dfC, CFG)
        return sig.iloc[idx], sig.index[idx], sig


def handle_tick(st, k):
    now = dt.datetime.now().timestamp()
    st.upsert_bar(k)
    is_closed = bool(k["x"])
    if ab.weekend_muted():   # XAU 주말(금 휴장) 발송 억제 (df0는 upsert로 최신 유지)
        return

    if is_closed:
        st.maybe_refresh_htf()
        row, when, sig = st.evaluate(-1)
        e = enrich(row, sig)
        d = e.get("direction_active", e["direction"])   # 확정 막돌파: 에지 아닌 레벨+fresh기준(2026-08-07)
        breakout = bool(d) and e.get("fresh_long" if d == "LONG" else "fresh_short", 0) >= 3
        aligned = breakout and ((e["r1_long"] and e["r2_long"]) if d == "LONG"
                                else (e["r1_short"] and e["r2_short"]))
        must_ok = bool(d) and all((e["must_long"] if d == "LONG" else e["must_short"]).values())  # 잠정과 동일 필수2 가드
        if d and breakout and must_ok and getattr(handle_tick, "send_confirm", True):
            if pb_ready() and ab.pullback_note(e, d == "LONG"):   # 눌림목/반등목 → 눌림목방에만
                if st.sent_pb == (d, when):
                    print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: 눌림목방 중복")
                elif not st.pb_ok(d, when):
                    print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: 눌림목방 30분내 같은방향 억제")
                else:
                    emit_pullback(fmt_signal(e, when, provisional=False, active_dir=d))
                    st.last_dir = d; st.sent_pb = (d, when); st.pb_last_sent[d] = when
            elif aligned:
                if st.sent_key == (d, when):
                    print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: 맥점방 중복")
                elif not st.maek_ok(d, when):
                    print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: 맥점방 30분내 같은방향 억제")
                else:
                    emit(fmt_signal(e, when, provisional=False, active_dir=d))
                    st.last_dir = d; st.sent_key = (d, when); st.maek_last_sent[d] = when
            elif bo_ready() and st.sent_bo != (d, when) and st.bo_ok(d, when):
                emit_breakout(fmt_signal(e, when, provisional=False, active_dir=d))
                st.sent_bo = (d, when); st.bo_last_dir = d; st.bo_last_sent[d] = when
            else:
                print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: 막돌파방 억제")
        else:
            reason = ("신호없음" if not d else "막돌파아님" if not breakout else "필수미충족" if not must_ok else "억제")
            print(f"[ws-5m] {kst(when):%m-%d %H:%M} 마감: {reason} | "
                  f"dir={e['direction']}/act={e.get('direction_active')} "
                  f"freshL={e.get('fresh_long')} freshS={e.get('fresh_short')} "
                  f"mustL={sum(e['must_long'].values())} mustS={sum(e['must_short'].values())}")
        divergence.check(st.df0, SYMBOL, TF, _token(),
                         os.environ.get("TELEGRAM_CHAT_ID_1M"),
                         os.environ.get("TELEGRAM_THREAD_ID_5M"), st.sent_div)
        st.alerted_bar = None
        st.alerted_dirs = set()
        return

    if now - st.last_recompute < RECOMPUTE_MIN_SEC:
        return
    st.last_recompute = now
    row, when, sig = st.evaluate(-1)
    e = enrich(row, sig)
    if when != st.alerted_bar:
        st.alerted_bar = when
        st.alerted_dirs = set()
    d = e.get("direction_active", e["direction"])
    if not d:
        return
    breakout = e.get("fresh_long" if d == "LONG" else "fresh_short", 0) >= 3
    if not breakout:
        return
    aligned = (e["r1_long"] and e["r2_long"]) if d == "LONG" else (e["r1_short"] and e["r2_short"])
    is_pb = bool(pb_ready() and ab.pullback_note(e, d == "LONG"))   # 눌림목/반등목 → 눌림목방에만
    if is_pb:
        gate = st.sent_pb != (d, when) and st.pb_ok(d, when)
    elif aligned:
        gate = d not in st.alerted_dirs
    else:
        gate = st.sent_bo != (d, when) and st.bo_ok(d, when)
    if not gate:
        return
    must_ok = all((e["must_long"] if d == "LONG" else e["must_short"]).values())
    if not must_ok:
        st.alerted_dirs.add(d)
        return
    mins_left = (when + pd.Timedelta(TF) - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
    mins_left = max(0, mins_left)
    if mins_left < PROV_MIN_MINS_LEFT:
        st.alerted_dirs.add(d)
        return
    card = fmt_signal(e, when, provisional=True, mins_left=mins_left, active_dir=d)
    if is_pb:
        emit_pullback(card)
        st.sent_pb = (d, when); st.pb_last_sent[d] = when; st.last_dir = d
    elif aligned:
        if st.maek_ok(d, when):
            emit(card)
            st.alerted_dirs.add(d); st.sent_key = (d, when); st.last_dir = d; st.maek_last_sent[d] = when
        else:
            st.alerted_dirs.add(d)   # 맥점방 30분내 같은방향 억제
    else:
        emit_breakout(card)
        st.sent_bo = (d, when); st.bo_last_dir = d; st.bo_last_sent[d] = when


async def run(send_confirm=True):
    handle_tick.send_confirm = send_confirm
    st = LiveState()
    print("기준 히스토리 로드 중…")
    st.load_base()
    print(f"📡 맥점 웹소켓 감시 시작 — {SYMBOL} {TF} (맥점방/막돌파방)")
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                async for msg in ws:
                    k = json.loads(msg).get("k")
                    if k:
                        try:
                            handle_tick(st, k)
                        except Exception as ex:
                            print("판정 오류:", ex)
        except Exception as ex:
            print("웹소켓 끊김, 5초 후 재연결:", ex)
            await asyncio.sleep(5)
            try:
                st.df0 = data.get_history(SYMBOL, TF, bars=1000)
            except Exception:
                pass


if __name__ == "__main__":
    send_confirm = "--no-confirm" not in sys.argv
    try:
        if os.environ.get("FEED_MODE") == "poll":   # 선물 WS 차단 환경(XAU) → REST 폴링
            import poll_feed
            poll_feed.run_poll(sys.modules[__name__], send_confirm)
        else:
            asyncio.run(run(send_confirm))
    except KeyboardInterrupt:
        print("\n종료")
