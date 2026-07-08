# -*- coding: utf-8 -*-
"""맥점 웹소켓 실시간 감시 — 일봉(1d) 전용.

ws_watch.py(1h)·ws_watch_1m.py(15m)와 동일 구조. 신호는 드물지만 큰 그림 맥점 포착용.
알림 채널: 일봉 전용 (TELEGRAM_TOKEN_1D / TELEGRAM_CHAT_ID_1D).
일봉 마감 = 바이낸스 UTC 00:00 = 한국시간 09:00.

실행:
  python ws_watch_1d.py                 # 예비+확정 모두 발송
  python ws_watch_1d.py --no-confirm    # 예비만 발송
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
import alert_bot as ab            # tg_html(HTML 이스케이프) 재사용

# ── 타임프레임 설정 ────────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
TF = "1d"
HTF = ("3d", "1w", "1M")
HTF_LABELS = ("3일", "주봉", "월봉")
KST = ZoneInfo("Asia/Seoul")

CFG = {
    "atr_period": 14,
    "rci_long": 26,
    "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3,
    "trend_pivot": 8,
    "rem_req": 3,    # 나머지6 중 3개(대각선 제외)
    "atr_stop_mult": 2.0,
    "limit_offset": 0.0003,
    "trend_lookback": 100,
}

WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_{TF}"
HTF_REFRESH_SEC = 3600       # 상위TF 갱신 주기(1시간 — 일봉이라 여유)
RECOMPUTE_MIN_SEC = 600      # 잠정 재계산 최소 간격(10분)
PROV_MIN_MINS_LEFT = 60      # 마감 1시간 미만이면 잠정 억제


# ── 유틸 ───────────────────────────────────────────────────────────────────────
def kst(ts):
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)


def tg_send(text):
    token = os.environ.get("TELEGRAM_TOKEN_1D")  # 일봉 전용 채널
    chat = os.environ.get("TELEGRAM_CHAT_ID_1D")
    if not token or not chat:
        print("⚠️ 일봉봇 텔레그램 미설정: TELEGRAM_TOKEN_1D / TELEGRAM_CHAT_ID_1D 없음 (콘솔만 출력).")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat, "text": ab.tg_html(text), "parse_mode": "HTML"}
    thread = os.environ.get("TELEGRAM_THREAD_ID_1D")   # 그룹 토픽(1일봉)
    if thread:
        payload["message_thread_id"] = thread
    try:
        r = requests.post(url, data=payload, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램(1d) 전송 성공")
            return True
        print(f"⚠️ HTML 전송 실패({j.get('description')}) → 평문 재시도")
        clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        plain = {"chat_id": chat, "text": clean}
        if thread:
            plain["message_thread_id"] = thread
        r = requests.post(url, data=plain, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램(1d) 전송 성공(평문)")
            return True
        print(f"❌ 텔레그램(1d) 전송 실패: {j.get('description')}")
        return False
    except Exception as e:
        print("❌ 텔레그램(1d) 전송 오류:", e)
        return False


def emit(text):
    clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(clean)
    tg_send(text)


# ── 포맷터 ─────────────────────────────────────────────────────────────────────
def fmt_checks(checks):
    return "\n".join(f"  {'✅' if v else '❌'} {k}" for k, v in checks.items())


def fmt_signal(e, when, provisional=False, mins_left=None, active_dir=None):
    d = active_dir if active_dir is not None else e["direction"]
    long_ = d == "LONG"
    side = "🟢 롱(LONG)" if long_ else "🔴 숏(SHORT)"
    px = e["close"]
    limit = px * (1 - CFG["limit_offset"]) if long_ else px * (1 + CFG["limit_offset"])
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
    aligned = (e["bias"] > 0) == long_ and abs(e["bias"]) >= 2
    # 배지: 🎯 막돌파(타이밍) / ⚡ 급반전 / ⭐ 전조건 정렬 / 🔥 강신호
    badge = ""
    fresh = e.get("fresh_long" if long_ else "fresh_short", 0)
    if fresh >= 3:
        badge += f"🎯 <b>막돌파 맥점</b> — 핵심 트리거 동시돌파({fresh}/4, 최근 3봉)\n"
    if e.get("fast3_long" if long_ else "fast3_short", False):
        badge += "⚡ <b>급반전 후보</b> — MACD·스토·RCI단 동시 점등\n"
    rem_n = sum(rem.values()); n_tot = len(rem)
    if rem_n == n_tot:
        badge += f"⭐ <b>전조건 정렬</b>(나머지 {rem_n}/{n_tot}, 그린26 포함) — 추세 진행 중, 추격 주의\n"
    elif rem_n == n_tot - 1:
        badge += f"🔥 <b>강신호</b> — 나머지 {rem_n}/{n_tot}\n"
    if provisional:
        if mins_left is not None and mins_left >= 120:
            left = f"마감 {mins_left/60:.0f}시간 전"
        elif mins_left is not None:
            left = f"마감 {mins_left:.0f}분 전"
        else:
            left = "마감 전"
        head = (f"⏱ {kst(when):%Y-%m-%d} 일봉 형성중 · {left} (마감 09:00 KST)\n"
                f"⚠️ <b>마감 전 현재가 기준</b> — 봉 마감까지 되돌리면 취소 가능\n")
    else:
        head = f"⏱ {kst(when):%Y-%m-%d} KST (일봉 마감)\n"
    dir_line = f"<b>{side} {'예비신호 (잠정)' if provisional else '진입신호'}</b> — {SYMBOL} ({TF})\n"
    top_warn = "📏 <b>진입 전 추세선·X선·가로 매물대·채널 확인 필수! (모든 조건에 우선)</b>\n"
    fib_warn = "" if aligned else "⚠️ <b>역추세 — 큰 추세의 되돌림일 수 있음. 다이버전스 확인 & 피보나치로 타점 계산 후 신중 진입!</b>\n"
    return (
        dir_line + badge + head +
        f"📊 <b>상위TF 방향</b> {'✅추세정렬' if aligned else '⚠️역추세—신중'}\n"
        f"   · {HTF_LABELS[0]} {e['tf_1h']} / {HTF_LABELS[1]} {e['tf_4h']} / {HTF_LABELS[2]} {e['tf_1d']}\n"
        f"{fib_warn}"
        f"━━━━━━━━━━━━━\n"
        f"💵 현재가 {px:,.1f}\n"
        f"🛑 손절 {sl_txt} → 리스크 {risk_pct:.2f}%\n"
        f"━━━━━━━━━━━━━\n"
        f"{top_warn}"
        f"<b>필수 {sum(must.values())}/2</b>\n{fmt_checks(must)}\n"
        f"<b>나머지 {sum(rem.values())}/{len(rem)} (≥{CFG['rem_req']} 필요)</b>\n{fmt_checks(rem)}\n"
        f"<i>판독이지 매매권유 아님. 최종 판단은 본인.</i>"
    )


def enrich(row, sig):
    return strategy.explain(row, CFG)


# ── 라이브 상태 ────────────────────────────────────────────────────────────────
class LiveState:
    def __init__(self):
        self.df1d = None
        self.df3d = self.df1w = self.df1M = None
        self.htf_loaded_at = 0.0
        self.alerted_bar = None
        self.alerted_dirs = set()
        self.last_dir = None          # 직전 발송 방향 — 변곡 전까지 같은 방향 억제
        self.sent_key = None          # 마지막 발송 (방향, 봉) — 같은 봉 중복 방지
        self.last_recompute = 0.0

    def same_dir_blocked(self, d, when):
        """직전 발송과 같은 방향이면 억제. 반대 신호(변곡)가 나와야만 재허용."""
        return d == self.last_dir

    def load_base(self):
        self.df1d = data.get_history(SYMBOL, TF, bars=600)
        self._load_htf()

    def _load_htf(self):
        self.df3d = data.get_history(SYMBOL, HTF[0], bars=300)
        self.df1w = data.get_history(SYMBOL, HTF[1], bars=300)
        self.df1M = data.get_history(SYMBOL, HTF[2], bars=200)
        self.htf_loaded_at = dt.datetime.now().timestamp()

    def maybe_refresh_htf(self):
        if dt.datetime.now().timestamp() - self.htf_loaded_at > HTF_REFRESH_SEC:
            self._load_htf()

    def upsert_bar(self, k):
        t = pd.to_datetime(k["t"], unit="ms", utc=True)
        row = {"open": float(k["o"]), "high": float(k["h"]),
               "low": float(k["l"]), "close": float(k["c"]),
               "volume": float(k["v"])}
        self.df1d.loc[t] = row
        self.df1d = self.df1d[~self.df1d.index.duplicated(keep="last")].sort_index().tail(600)
        return t

    def evaluate(self, idx):
        sig = strategy.build_signals(self.df1d, self.df3d, self.df1w, self.df1M, CFG)
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
        if d and not st.same_dir_blocked(d, when) and getattr(handle_tick, "send_confirm", True):
            emit(fmt_signal(e, when, provisional=False))
            st.last_dir = d
            st.sent_key = (d, when)
        else:
            why = "방향전환 없음(억제중)" if d and st.same_dir_blocked(d, when) else (d or "신호없음")
            print(f"[ws-1d] {kst(when):%m-%d} 마감: {why}")
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
    if d and d not in st.alerted_dirs and not st.same_dir_blocked(d, when):
        must_ok = all((e["must_long"] if d == "LONG" else e["must_short"]).values())
        if not must_ok:
            st.alerted_dirs.add(d)
            return
        mins_left = (when + pd.Timedelta(TF) - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
        mins_left = max(0, mins_left)
        if mins_left < PROV_MIN_MINS_LEFT:
            st.alerted_dirs.add(d)
            return
        emit(fmt_signal(e, when, provisional=True, mins_left=mins_left, active_dir=d))
        st.alerted_dirs.add(d)
        st.last_dir = d
        st.sent_key = (d, when)


async def run(send_confirm=True):
    handle_tick.send_confirm = send_confirm
    st = LiveState()
    print("기준 히스토리 로드 중…")
    st.load_base()
    print(f"📡 맥점 웹소켓 감시 시작 — {SYMBOL} {TF} "
          f"({'2단계: 예비+확정' if send_confirm else '예비만'})"
          + ("" if os.environ.get("TELEGRAM_TOKEN_1D") else " (콘솔 모드)"))
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
                st.df1d = data.get_history(SYMBOL, TF, bars=600)
            except Exception:
                pass


if __name__ == "__main__":
    send_confirm = "--no-confirm" not in sys.argv
    try:
        asyncio.run(run(send_confirm))
    except KeyboardInterrupt:
        print("\n종료")
