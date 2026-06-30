# -*- coding: utf-8 -*-
"""맥점 웹소켓 실시간 감시 — 1분봉 전용.

기존 ws_watch.py(1시간봉)와 별도로 동작한다.
Telegram 채널도 분리: TELEGRAM_TOKEN_1M / TELEGRAM_CHAT_ID_1M 환경변수.

2단계 알림:
  ⚡ 1단계(예비/잠정) — 형성 중 1분봉의 현재가가 조건을 충족하는 순간 발송.
  ✅ 2단계(확정)      — 1분봉 마감(k.x=true) 즉시 발송.

실행:
  python ws_watch_1m.py                 # 예비+확정 모두 발송
  python ws_watch_1m.py --no-confirm    # 예비만 발송
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
import indicators as ind

# ── 타임프레임 설정 ────────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
TF = "15m"
HTF = ("30m", "1h", "2h")
HTF_LABELS = ("30분", "1시간", "2시간")
KST = ZoneInfo("Asia/Seoul")

CFG = {
    "atr_period": 14,
    "rci_long": 26,
    "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3,
    "trend_pivot": 3,    # 대각선 스윙 강도(3=20일선에 가장 근접)
    "rem_req": 3,
    "atr_stop_mult": 2.0,
    "limit_offset": 0.0003,
    "trend_lookback": 100,
}

WS_URL = f"wss://stream.binance.com:9443/ws/{SYMBOL.lower()}@kline_{TF}"
HTF_REFRESH_SEC = 300        # 상위TF 갱신 주기
RECOMPUTE_MIN_SEC = 60       # 잠정 재계산 최소 간격 (중복 방지)
PROV_MIN_MINS_LEFT = 5       # 잔여시간 이 미만이면 잠정신호 억제 (분)


# ── 유틸 ───────────────────────────────────────────────────────────────────────
def kst(ts):
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)


def tg_send(text):
    token = os.environ.get("TELEGRAM_TOKEN_1M")
    chat = os.environ.get("TELEGRAM_CHAT_ID_1M")
    if not token or not chat:
        print("⚠️ 1분봇 텔레그램 미설정: TELEGRAM_TOKEN_1M / TELEGRAM_CHAT_ID_1M 환경변수 없음 (콘솔만 출력).")
        return False
    clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": clean}, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램(1m) 전송 성공")
            return True
        print(f"❌ 텔레그램(1m) 전송 실패: {j.get('description')}")
        return False
    except Exception as e:
        print("❌ 텔레그램(1m) 전송 오류:", e)
        return False


def emit(text):
    clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(clean)
    tg_send(text)


# ── 추세선 레벨 ────────────────────────────────────────────────────────────────
def trendline_level(sig, kind, cfg):
    df = sig.tail(cfg["trend_lookback"])
    piv = (ind.swing_high(df, cfg["pivot_left"], cfg["pivot_right"]) if kind == "res"
           else ind.swing_low(df, cfg["pivot_left"], cfg["pivot_right"])).dropna()
    if len(piv) < 2:
        return None
    pos = df.index.get_indexer([piv.index[-2], piv.index[-1]])
    (i1, y1), (i2, y2) = (pos[0], piv.iloc[-2]), (pos[1], piv.iloc[-1])
    if i2 == i1:
        return None
    slope = (y2 - y1) / (i2 - i1)
    return float(y2 + slope * (len(df) - 1 - i2))


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
    # 강도 배지: 나머지 7/7(그린26 포함)=베스트 / 6/7=강신호
    rem_n = sum(rem.values()); n_tot = len(rem)
    if rem_n == n_tot:
        badge = f"⭐ <b>베스트 타점</b> — 전조건 충족(나머지 {rem_n}/{n_tot}, 그린26 포함)\n"
    elif rem_n == n_tot - 1:
        badge = f"🔥 <b>강신호</b> — 나머지 {rem_n}/{n_tot}\n"
    else:
        badge = ""
    if provisional:
        left = f"마감 {mins_left:.0f}분 전" if mins_left is not None else "마감 전"
        head = (f"<b>⚡ {side} 예비신호 (잠정)</b> — {SYMBOL} ({TF})\n"
                f"⏱ {kst(when):%Y-%m-%d %H:%M:%S} KST 봉 형성중 · {left}\n"
                f"⚠️ <b>마감 전 현재가 기준</b> — 봉 마감까지 되돌리면 취소 가능\n")
    else:
        head = (f"<b>{side} 진입신호</b> — {SYMBOL} ({TF})\n"
                f"⏱ {kst(when):%Y-%m-%d %H:%M:%S} KST ({TF} 마감)\n")
    return (
        badge + head +
        f"📊 <b>상위TF 방향</b> {'✅추세정렬' if aligned else '⚠️역추세—신중'}\n"
        f"   · {HTF_LABELS[0]} {e['tf_1h']} / {HTF_LABELS[1]} {e['tf_4h']} / {HTF_LABELS[2]} {e['tf_1d']}\n"
        f"━━━━━━━━━━━━━\n"
        f"💵 현재가 {px:,.1f}\n"
        f"📥 지정가 진입 {limit:,.1f} (1호가 {'아래' if long_ else '위'})\n"
        f"🛑 손절 {sl_txt} → 리스크 {risk_pct:.2f}%\n"
        f"━━━━━━━━━━━━━\n"
        f"<b>필수 {sum(must.values())}/2</b>\n{fmt_checks(must)}\n"
        f"<b>나머지 {sum(rem.values())}/{len(rem)} (≥{CFG['rem_req']} 필요)</b>\n{fmt_checks(rem)}\n"
        f"<i>판독이지 매매권유 아님. 1분봉 단기신호. 최종 판단은 본인.</i>"
    )


# ── 신호 계산 ──────────────────────────────────────────────────────────────────
def enrich(row, sig):
    return strategy.explain(row, CFG)


# ── 라이브 상태 ────────────────────────────────────────────────────────────────
class LiveState:
    def __init__(self):
        self.df1m = None
        self.df5m = self.df15m = self.df1h = None
        self.htf_loaded_at = 0.0
        self.alerted_bar = None
        self.alerted_dirs = set()
        self.last_dir = None          # 직전 발송 방향(봉 넘어 유지) — 같은 방향 연속 억제
        self.last_recompute = 0.0

    def load_base(self):
        self.df1m = data.get_history(SYMBOL, TF, bars=600)
        self._load_htf()

    def _load_htf(self):
        self.df5m  = data.get_history(SYMBOL, HTF[0], bars=400)
        self.df15m = data.get_history(SYMBOL, HTF[1], bars=300)
        self.df1h  = data.get_history(SYMBOL, HTF[2], bars=200)
        self.htf_loaded_at = dt.datetime.now().timestamp()

    def maybe_refresh_htf(self):
        if dt.datetime.now().timestamp() - self.htf_loaded_at > HTF_REFRESH_SEC:
            self._load_htf()

    def upsert_bar(self, k):
        t = pd.to_datetime(k["t"], unit="ms", utc=True)
        row = {"open": float(k["o"]), "high": float(k["h"]),
               "low": float(k["l"]), "close": float(k["c"]),
               "volume": float(k["v"])}
        self.df1m.loc[t] = row
        self.df1m = self.df1m[~self.df1m.index.duplicated(keep="last")].sort_index().tail(600)
        return t

    def evaluate(self, idx):
        sig = strategy.build_signals(self.df1m, self.df5m, self.df15m, self.df1h, CFG)
        return sig.iloc[idx], sig.index[idx], sig


def handle_tick(st, k):
    now = dt.datetime.now().timestamp()
    when_form = st.upsert_bar(k)
    is_closed = bool(k["x"])

    if is_closed:
        st.maybe_refresh_htf()
        row, when, sig = st.evaluate(-1)
        e = enrich(row, sig)
        d = e["direction"]
        # 직전 발송 방향과 같으면 연속 신호 → 억제(반대 신호가 끼면 다시 허용)
        if d and d != st.last_dir and getattr(handle_tick, "send_confirm", True):
            emit(fmt_signal(e, when, provisional=False))
            st.last_dir = d
        else:
            why = "방향전환 없음" if d and d == st.last_dir else (d or "신호없음")
            print(f"[ws-1m] {kst(when):%m-%d %H:%M:%S} 마감: {why}")
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
    # 억제: ① 같은 봉·같은 방향 중복(임계선 깜빡임) ② 직전 발송과 같은 방향 연속(봉 넘어 노이즈).
    #       중간에 반대 신호가 끼면 last_dir이 바뀌어 다음 동일방향은 다시 허용.
    if d and d not in st.alerted_dirs and d != st.last_dir:
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


async def run(send_confirm=True):
    handle_tick.send_confirm = send_confirm
    st = LiveState()
    print("기준 히스토리 로드 중…")
    st.load_base()
    print(f"📡 맥점 웹소켓 감시 시작 — {SYMBOL} {TF} "
         f"({'2단계: 예비+확정' if send_confirm else '예비만'})"
         + ("" if os.environ.get("TELEGRAM_TOKEN_1M") else " (콘솔 모드)"))
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
                st.df1m = data.get_history(SYMBOL, TF, bars=1000)
            except Exception:
                pass


if __name__ == "__main__":
    send_confirm = "--no-confirm" not in sys.argv
    try:
        asyncio.run(run(send_confirm))
    except KeyboardInterrupt:
        print("\n종료")
