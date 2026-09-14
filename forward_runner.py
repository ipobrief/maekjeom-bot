# -*- coding: utf-8 -*-
"""
트랙2 forward 검증 실행기 (독립) — 기존 알림봇은 안 건드림.

B설정(백테스트 검증): 10분봉 눌림목(막돌파 fresh≥3 + 상위TF MACD 정렬≥2/3) 진입,
  청산=본절런너(초기 전저점손절 → +0.3% 유리 시 본절 → 반대신호 청산).

동작:
  · 매 POLL초: executor.check() (마크가격으로 본절/손절 감시)
  · 새 10분봉 마감 시: 신호 재계산 → (보유중이면)반대신호 청산 / (플랫이면)눌림목 진입
기본 테스트넷(가짜돈). 실계좌는 BINANCE_TESTNET=0 명시해야 함.
API키는 환경변수(BINANCE_API_KEY/SECRET). Ctrl+C로 중단.
"""
import os
import time
import logging

import numpy as np
import data
import strategy
from trend_executor import TrendExecutor, is_weekend_kst

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%m-%d %H:%M:%S")
log = logging.getLogger("forward")

SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
POLL = int(os.environ.get("POLL_SEC", "20"))
CFG = {  # ws_watch_10m 지표설정과 동일
    "atr_period": 14, "rci_long": 26, "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3, "trend_pivot": 8, "rem_req": 3,
    "atr_stop_mult": 2.0, "limit_offset": 0.0003, "trend_lookback": 100,
    "fresh_bars": 3,   # 10분봉은 상위TF라 fresh_bars=3 (CLAUDE.md)
}


def latest_signal():
    """최근 마감된 10분봉의 B 신호를 반환: (direction|None, swing_sl, opp_exit_long, opp_exit_short)."""
    d10 = data.get_history(SYMBOL, "10m", bars=600)
    d30 = data.get_history(SYMBOL, "30m", bars=300)
    d1h = data.get_history(SYMBOL, "1h", bars=200)
    d2h = data.get_history(SYMBOL, "2h", bars=200)
    sig = strategy.build_signals(d10, d30, d1h, d2h, CFG)
    r = sig.iloc[-2]          # 마지막 '마감된' 봉 (마지막 행은 형성중)
    bar_time = sig.index[-2]
    # 진입 = 맥점(막돌파) — TV 화살표와 동일(막돌파 fresh≥3). HTF필터는 옵션(기본 끔).
    use_htf = os.environ.get("USE_HTF_FILTER", "0").lower() not in ("0", "false", "no")
    tmL = sum(int(r[f"boss_m0_{i}"] and r[f"boss_mu_{i}"]) for i in (1, 2, 3))
    tmS = sum(int((not r[f"boss_m0_{i}"]) and (not r[f"boss_mu_{i}"])) for i in (1, 2, 3))
    b_long = bool(r["long"]) and r["fresh_long"] >= 3 and (tmL >= 2 if use_htf else True)
    b_short = bool(r["short"]) and r["fresh_short"] >= 3 and (tmS >= 2 if use_htf else True)
    direction = "long" if b_long else ("short" if b_short else None)
    # 손절선(전저점/전고점), 무효시 ATR 대체
    if direction:
        is_long = direction == "long"
        swing = r["swing_low"] if is_long else r["swing_high"]
        px = r["close"]
        if (swing != swing) or (is_long and swing >= px) or (not is_long and swing <= px):
            swing = px - r["atr"] * CFG["atr_stop_mult"] * (1 if is_long else -1)
    else:
        swing = None
    return direction, swing, bool(r["long_exit"]), bool(r["short_exit"]), bar_time, r["close"]


def main():
    ex = TrendExecutor()
    log.info("forward 시작: %s | %s | 증거금 %s×%sx | 본절+%.1f%% | 주말스킵 %s",
             SYMBOL, "테스트넷" if ex.ex.testnet else "★실계좌★",
             ex.cfg["margin_per_trade"], ex.cfg["leverage"], ex.cfg["be_after"] * 100, ex.cfg["weekend_off"])
    last_bar = None
    while True:
        try:
            # 1) 항상 포지션 감시(본절/손절)
            ex.check()
            # 2) 새 봉 마감 시에만 신호 처리
            direction, swing, oxl, oxs, bar_time, close = latest_signal()
            if bar_time != last_bar:
                last_bar = bar_time
                in_pos = ex.state is not None
                log.info("봉마감 %s close=%.1f | 신호=%s | 보유=%s",
                         bar_time.tz_convert("Asia/Seoul").strftime("%m-%d %H:%M"),
                         close, direction or "-", ex.state["dir"] if in_pos else "-")
                if in_pos:
                    is_long = ex.state["dir"] == "long"
                    if (oxl if is_long else oxs):   # 반대신호 → 청산
                        log.info("반대신호 감지 → 청산")
                        ex.exit_now("opposite_signal")
                elif direction and not (ex.cfg["weekend_off"] and is_weekend_kst()):
                    log.info("눌림목 진입 신호(%s) → enter", direction)
                    ex.enter(direction, swing)
        except Exception as e:
            log.warning("루프 오류: %s", e)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
