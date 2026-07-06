# -*- coding: utf-8 -*-
"""맥점 웹소켓 실시간 감시 — 폴링 없이 바이낸스 kline 스트림으로 즉시 판정.

2단계 알림:
  ⚡ 1단계(예비/잠정) — 형성 중 15분봉의 현재가가 조건을 막 충족하는 순간 발송.
                        봉 마감 전이라 되돌리면 취소될 수 있음.
  ✅ 2단계(확정)      — 15분봉이 실제로 마감(k.x=true)되는 즉시 발송.

지표 계산엔 과거 봉 전체가 필요하므로:
  · 시작 시 REST로 기준 히스토리 1회 로드(15m/1h/4h/1d)
  · 웹소켓으로 형성 중 봉의 OHLC만 실시간 갱신해 재계산
  · 봉 마감 시 그 봉을 확정 편입하고 상위TF는 주기적으로 갱신

바이낸스 현물 스트림(공개, 무료, API키 불필요): wss://stream.binance.com:9443
※ 미국 IP는 451 차단될 수 있음 → 사용자 PC(국내)에서 상시 실행 권장.
   (GitHub Actions 같은 미국 러너에선 동작 안 함)

실행:
  python ws_watch.py                 # 2단계(예비+확정) 모두 발송
  python ws_watch.py --no-confirm    # 예비신호만 (확정은 기존 cron에 맡길 때)
"""
import os
import sys
import json
import asyncio
import datetime as dt
import pandas as pd
import websockets

import data
import strategy
import alert_bot as ab

WS_URL = f"wss://stream.binance.com:9443/ws/{ab.SYMBOL.lower()}@kline_{ab.TF}"
HTF_REFRESH_SEC = 300        # 상위TF(1h/4h/1d) 갱신 주기
RECOMPUTE_MIN_SEC = 120      # 잠정 재계산 최소 간격 (중복 방지)
PROV_MIN_MINS_LEFT = 15      # 잔여시간 이 미만이면 잠정신호 억제 (분)


class LiveState:
    """기준 히스토리 + 형성봉 실시간 갱신 상태."""

    def __init__(self):
        self.df15 = None
        self.df1h = self.df4h = self.df1d = None
        self.htf_loaded_at = 0.0
        self.alerted_bar = None       # 예비신호 추적 중인 형성봉 open_time
        self.alerted_dirs = set()     # 이 형성봉에서 이미 알린 방향들(중복 방지)
        self.last_dir = None          # 직전 발송 방향(봉 넘어 유지) — 같은 방향 연속 억제
        self.sent_key = None          # 마지막 발송 (방향, 봉) — 잠정→확정 같은 봉 중복 방지
        self.last_recompute = 0.0

    def same_dir_blocked(self, d, when):
        """직전 발송과 같은 방향이면 억제. 반대 신호(변곡)가 나와야만 재허용.
        초기 맥점 돌파가 핵심 — 시간이 지나도 같은 방향 재알람은 의미 없음(2026-07-04)."""
        return d == self.last_dir

    def load_base(self):
        self.df15 = data.get_history(ab.SYMBOL, ab.TF, bars=600)
        self._load_htf()

    def _load_htf(self):
        self.df1h = data.get_history(ab.SYMBOL, ab.HTF[0], bars=400)
        self.df4h = data.get_history(ab.SYMBOL, ab.HTF[1], bars=300)
        self.df1d = data.get_history(ab.SYMBOL, ab.HTF[2], bars=200)
        self.htf_loaded_at = dt.datetime.now().timestamp()

    def maybe_refresh_htf(self):
        if dt.datetime.now().timestamp() - self.htf_loaded_at > HTF_REFRESH_SEC:
            self._load_htf()

    def upsert_bar(self, k):
        """웹소켓 kline로 형성봉을 갱신(같은 open_time이면 덮어쓰기, 새 봉이면 추가)."""
        t = pd.to_datetime(k["t"], unit="ms", utc=True)
        row = {"open": float(k["o"]), "high": float(k["h"]),
               "low": float(k["l"]), "close": float(k["c"]),
               "volume": float(k["v"])}
        self.df15.loc[t] = row
        self.df15 = self.df15[~self.df15.index.duplicated(keep="last")].sort_index().tail(600)
        return t

    def evaluate(self, idx):
        """idx=-1 형성봉(잠정) / idx=-2 마지막 마감봉(확정). (row, when, sig) 반환."""
        sig = strategy.build_signals(self.df15, self.df1h, self.df4h, self.df1d, ab.CFG)
        return sig.iloc[idx], sig.index[idx], sig


def handle_tick(st, k):
    """웹소켓 메시지 1건 처리. 형성봉 갱신 → 잠정/확정 판정 및 발송."""
    now = dt.datetime.now().timestamp()
    when_form = st.upsert_bar(k)
    is_closed = bool(k["x"])

    if is_closed:
        # 방금 봉이 마감됨 → 확정 판정 (마지막 마감봉 = -1, 새 형성봉은 아직 없음)
        st.maybe_refresh_htf()
        row, when, sig = st.evaluate(-1)        # 방금 마감된 봉
        e = ab.enrich(row, sig)
        d = e["direction"]
        # 같은 방향 연속은 억제. 단 🎯/⚡ 특수 신호가 '막 켜진' 봉은 예외(에지 1회 발송).
        allowed = d and not st.same_dir_blocked(d, when) \
                  and st.sent_key != (d, when)
        if allowed and getattr(handle_tick, "send_confirm", True):
            ab.emit(ab.fmt_signal(e, when, provisional=False))
            st.last_dir = d
            st.sent_key = (d, when)
        else:
            why = "방향전환 없음(억제중)" if d and st.same_dir_blocked(d, when) else (d or "신호없음")
            print(f"[ws] {ab.kst(when):%m-%d %H:%M} 마감: {why} (확정 점검)")
        # 봉이 바뀌었으니 예비신호 추적 리셋
        st.alerted_bar = None
        st.alerted_dirs = set()
        return

    # 형성 중 봉 → 잠정 판정 (재계산 간격 제한)
    if now - st.last_recompute < RECOMPUTE_MIN_SEC:
        return
    st.last_recompute = now
    row, when, sig = st.evaluate(-1)
    e = ab.enrich(row, sig)
    # 새 형성봉이면 알림 이력 초기화
    if when != st.alerted_bar:
        st.alerted_bar = when
        st.alerted_dirs = set()
    d = e.get("direction_active", e["direction"])
    # 억제: ① 같은 봉·같은 방향 중복(임계선 깜빡임) ② 직전 발송과 같은 방향 연속(봉 넘어 노이즈).
    #       반대 신호(변곡)가 나와야 재허용. 단 🎯/⚡ 특수 신호가 막 켜진 봉은 예외(에지 1회).
    if d and d not in st.alerted_dirs and not st.same_dir_blocked(d, when):
        # 필수조건(선행스팬1·20일선)이 실제로 충족된 경우만 발송
        must_ok = all((e["must_long"] if d == "LONG" else e["must_short"]).values())
        if not must_ok:
            st.alerted_dirs.add(d)
            return
        mins_left = (when + pd.Timedelta(ab.TF) - pd.Timestamp.now(tz="UTC")).total_seconds() / 60
        mins_left = max(0, mins_left)
        if mins_left < PROV_MIN_MINS_LEFT:
            st.alerted_dirs.add(d)
            return
        ab.emit(ab.fmt_signal(e, when, provisional=True, mins_left=mins_left, active_dir=d))
        st.alerted_dirs.add(d)
        st.last_dir = d
        st.sent_key = (d, when)


async def run(send_confirm=True):
    handle_tick.send_confirm = send_confirm
    st = LiveState()
    print("기준 히스토리 로드 중…")
    st.load_base()
    print(f"📡 맥점 웹소켓 감시 시작 — {ab.SYMBOL} {ab.TF} "
          f"({'2단계: 예비+확정' if send_confirm else '예비만'})"
          + ("" if os.environ.get("TELEGRAM_TOKEN") else " (콘솔 모드)"))
    while True:  # 끊기면 자동 재연결
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
                st.df15 = data.get_history(ab.SYMBOL, ab.TF, bars=600)  # 공백 메움
            except Exception:
                pass


if __name__ == "__main__":
    send_confirm = "--no-confirm" not in sys.argv
    try:
        asyncio.run(run(send_confirm))
    except KeyboardInterrupt:
        print("\n종료")
