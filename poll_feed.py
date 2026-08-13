# -*- coding: utf-8 -*-
"""REST 폴링 피드 드라이버 — 웹소켓 대신 fapi klines를 주기 폴링해 handle_tick에 공급.

용도: Binance **선물 웹소켓(fstream)이 IP 차단**된 환경(예: Oracle Cloud)에서 XAUUSDT 등
선물 종목을 감시할 때. 선물 REST(fapi)는 허용되므로 폴링으로 대체한다.
BTC(현물 WS 정상)는 기존 웹소켓 그대로 두고, XAU 서비스만 env `FEED_MODE=poll`로 이 경로를 탄다.

각 ws_watch 모듈의 handle_tick(st, k) / LiveState 를 그대로 재사용(라우팅·중복억제·카드 동일).
k = {"t":open_time_ms, "o","h","l","c","v", "x":봉마감여부} — 웹소켓 kline과 동일 형식으로 합성.
"""
import time
import data
import alert_bot as ab


def _poll_sec_for(tf):
    return 15 if tf in ("1m", "3m", "5m") else 30


def run_poll(mod, send_confirm=True, poll_sec=None):
    sym = getattr(mod, "SYMBOL", ab.SYMBOL)
    tf = getattr(mod, "TF", ab.TF)
    if poll_sec is None:
        poll_sec = _poll_sec_for(tf)
    mod.handle_tick.send_confirm = send_confirm
    st = mod.LiveState()
    print("기준 히스토리 로드 중…")
    st.load_base()
    print(f"📡 맥점 REST 폴링 감시 시작 — {sym} {tf} "
          f"(fstream 차단 대체, {poll_sec}s 폴링, "
          f"{'2단계: 예비+확정' if send_confirm else '예비만'})")
    prev_open = None
    while True:
        try:
            kl = data.fetch_klines(sym, tf, limit=3)
            if kl:
                last = kl[-1]
                cur_open = last[0]
                # 봉 마감 감지: 형성봉 open_time이 넘어가면 직전 봉(kl[-2])이 방금 마감된 것
                if prev_open is not None and cur_open != prev_open and len(kl) >= 2:
                    cb = kl[-2]
                    mod.handle_tick(st, {"t": cb[0], "o": cb[1], "h": cb[2],
                                         "l": cb[3], "c": cb[4], "v": cb[5], "x": True})
                # 형성 중 봉 → 잠정
                mod.handle_tick(st, {"t": last[0], "o": last[1], "h": last[2],
                                     "l": last[3], "c": last[4], "v": last[5], "x": False})
                prev_open = cur_open
        except Exception as ex:
            print("폴링 오류:", ex)
        time.sleep(poll_sec)
