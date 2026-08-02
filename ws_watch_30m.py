# -*- coding: utf-8 -*-
"""맥점 웹소켓 실시간 감시 — 30분봉. 맥점방(후행·전환 정렬)/막돌파방 풀 라우팅.
토큰 재사용: chris4h_bot(TELEGRAM_TOKEN_4H). 맥점방=TELEGRAM_THREAD_ID_30M, 막돌파방=TELEGRAM_BO_THREAD_30M.
실행: python ws_watch_30m.py  [--no-confirm]
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

SYMBOL = "BTCUSDT"
TF = "30m"
HTF = ("1h", "2h", "4h")
HTF_LABELS = ("1시간", "2시간", "4시간")
KST = ZoneInfo("Asia/Seoul")

CFG = {
    "atr_period": 14, "rci_long": 26, "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3, "trend_pivot": 8, "rem_req": 3,
    "atr_stop_mult": 2.0, "limit_offset": 0.0003, "trend_lookback": 100,
}

WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_{TF}"
HTF_REFRESH_SEC = 600
RECOMPUTE_MIN_SEC = 120
PROV_MIN_MINS_LEFT = 5


def kst(ts):
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)


def _token():
    return os.environ.get("TELEGRAM_TOKEN_4H") or os.environ.get("TELEGRAM_TOKEN_1D")


def tg_send(text):
    token = _token()
    chat = os.environ.get("TELEGRAM_CHAT_ID_4H") or os.environ.get("TELEGRAM_CHAT_ID_1D")
    if not token or not chat:
        print("⚠️ 30m봇 텔레그램 미설정 (콘솔만).")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": ab.tg_html(text), "parse_mode": "HTML"}
    thread = os.environ.get("TELEGRAM_THREAD_ID_30M")   # 맥점방 30분봉 토픽
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
    return bool(os.environ.get("TELEGRAM_CHAT_ID_BO") and os.environ.get("TELEGRAM_BO_THREAD_30M"))

def emit_breakout(text):
    print("🎯[막돌파방-30m]", text[:50])
    return ab.tg_send_room(text, _token(), os.environ.get("TELEGRAM_CHAT_ID_BO"),
                           os.environ.get("TELEGRAM_BO_THREAD_30M"))


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
    aligned_bias = (e["bias"] > 0) == long_ and abs(e["bias"]) >= 2
    badge = ""
    fresh = e.get("fresh_long" if long_ else "fresh_short", 0)
    aligned = fresh >= 3 and ((e.get("r1_long") and e.get("r2_long")) if long_
                              else (e.get("r1_short") and e.get("r2_short")))
    if fresh >= 3:
        if aligned:
            badge += "⭐ <b>맥점 완성</b> — 막돌파 + 후행·전환 정렬\n"
        else:
            badge += f"🎯 <b>막돌파 맥점</b> — 핵심 트리거 동시돌파({fresh}/3, 최근 2봉)\n"
    rem_n = sum(rem.values()); n_tot = len(rem)
    if rem_n == n_tot:
        badge += f"⭐ <b>전조건 정렬</b>(나머지 {rem_n}/{n_tot}, 그린26 포함) — 추세 진행 중, 추격 주의\n"
    elif rem_n == n_tot - 1:
        badge += f"🔥 <b>강신호</b> — 나머지 {rem_n}/{n_tot}\n"
    dir_line = f"<b>{side} {'예비신호 (잠정)' if provisional else '진입신호'}</b> — {SYMBOL} ({TF})\n"
    top_warn = ("📏 <b>[모든 것에 우선] 추세선은 직접 작도·판단!</b>\n"
                "<b>추세선 돌파가 먼저(델타지역 진입!)</b> → 그 후에야 선행스팬1·20일선·MACD·스토·RCI 돌파가 의미. 가로 매물대·채널·피보나치도 확인.\n")
    if provisional:
        left = f"마감 {mins_left:.0f}분 전" if mins_left is not None else "마감 전"
        head = f"⏱ {kst(when):%Y-%m-%d %H:%M} KST 봉 형성중 · {left}\n"
    else:
        head = f"⏱ {kst(when):%Y-%m-%d %H:%M} KST ({TF} 마감)\n"
    fib_warn = "" if aligned_bias else "⚠️ <b>역추세 — 큰 추세의 되돌림일 수 있음. 다이버전스 확인 & 피보나치로 타점 계산 후 신중 진입!</b>\n"
    box = "" if aligned else (f"<pre>🎯 막돌파 맥점  |  {'LONG' if long_ else 'SHORT'}  {fresh}/3</pre>\n" if fresh >= 3 else "")
    return (
        box + dir_line + badge + head +
        f"📊 <b>상위TF 방향</b> {'✅추세정렬' if aligned_bias else '⚠️역추세—신중'}\n"
        f"   · {HTF_LABELS[0]} {e['tf_1h']} / {HTF_LABELS[1]} {e['tf_4h']} / {HTF_LABELS[2]} {e['tf_1d']}\n"
        f"{fib_warn}"
        f"━━━━━━━━━━━━━\n"
        f"💵 현재가 {px:,.1f}\n"
        f"🛑 손절 {sl_txt} → 리스크 {risk_pct:.2f}%\n"
        f"🏁 <b>익절 = 추세선 이탈 시</b> (직접 보고 판단)\n"
        f"━━━━━━━━━━━━━\n"
        f"{top_warn}"
        f"<b>필수 {sum(must.values())}/2</b>\n{fmt_checks(must)}\n"
        f"<b>나머지 {sum(rem.values())}/{len(rem)} (≥{CFG['rem_req']} 필요)</b>\n{fmt_checks(rem)}\n"
        f"<i>판독이지 매매권유 아님. 최종 판단은 본인.</i>"
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

    def same_dir_blocked(self, d, when):
        return d == self.last_dir

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

    if is_closed:
        st.maybe_refresh_htf()
        row, when, sig = st.evaluate(-1)
        e = enrich(row, sig)
        d = e["direction"]
        breakout = bool(d) and e.get("fresh_long" if d == "LONG" else "fresh_short", 0) >= 3
        aligned = breakout and ((e["r1_long"] and e["r2_long"]) if d == "LONG"
                                else (e["r1_short"] and e["r2_short"]))
        if d and breakout and getattr(handle_tick, "send_confirm", True):
            if aligned:
                if not st.same_dir_blocked(d, when) and st.sent_key != (d, when):
                    emit(fmt_signal(e, when, provisional=False))
                    st.last_dir = d; st.sent_key = (d, when)
                else:
                    print(f"[ws-30m] {kst(when):%m-%d %H:%M} 마감: 맥점방 억제")
            elif bo_ready() and d != st.bo_last_dir and st.sent_bo != (d, when):
                emit_breakout(fmt_signal(e, when, provisional=False))
                st.sent_bo = (d, when); st.bo_last_dir = d
            else:
                print(f"[ws-30m] {kst(when):%m-%d %H:%M} 마감: 막돌파방 억제")
        else:
            print(f"[ws-30m] {kst(when):%m-%d %H:%M} 마감: {(d and '막돌파아님') or '신호없음'}")
        divergence.check(st.df0, SYMBOL, TF, _token(),
                         os.environ.get("TELEGRAM_CHAT_ID_4H") or os.environ.get("TELEGRAM_CHAT_ID_1D"),
                         os.environ.get("TELEGRAM_THREAD_ID_30M"), st.sent_div)
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
    if aligned:
        gate = d not in st.alerted_dirs and not st.same_dir_blocked(d, when)
    else:
        gate = d != st.bo_last_dir and st.sent_bo != (d, when)
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
    if aligned:
        emit(card)
        st.alerted_dirs.add(d); st.sent_key = (d, when); st.last_dir = d
    else:
        emit_breakout(card)
        st.sent_bo = (d, when); st.bo_last_dir = d


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
        asyncio.run(run(send_confirm))
    except KeyboardInterrupt:
        print("\n종료")
