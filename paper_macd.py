# -*- coding: utf-8 -*-
"""MACD 0선필터 '페이퍼(모의)' 봇 — 무필터 실거래봇과 A/B 비교용.
규칙: 진입=막돌파 + MACD정렬(롱은 MACD>0, 숏은 MACD<0). MACD 안 맞는 막돌파는 '무효'라
      진입도 청산도 안 함(반대 막돌파여도 MACD 안 맞으면 포지션 유지). 청산=유효 반대막돌파
      or 초기 전저점/전고점 손절(홀드, 본절없음). 유효 반대막돌파면 SAR(즉시 반대진입).
실주문 없음: 공개시세(1분 종가)로 체결 시뮬. 상태 paper_state_MACD.json, 로그 trades_MACD_BTCUSDT.jsonl.
20배·증거금$100·주말ON. 무필터봇과 동일 신호엔진, 차이는 MACD필터뿐.
"""
import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

import numpy as np
import data
import strategy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%m-%d %H:%M:%S")
log = logging.getLogger("paper_macd")
KST = timezone(timedelta(hours=9))

SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
POLL = int(os.environ.get("POLL_SEC", "20"))
LEV = int(os.environ.get("LEVERAGE", "20"))
MARGIN = float(os.environ.get("MARGIN_PER_TRADE", "100"))
WEEKEND_OFF = os.environ.get("WEEKEND_OFF", "1").lower() not in ("0", "false", "no")
USE_STOP = os.environ.get("USE_STOP", "1").lower() not in ("0", "false", "no")  # 0=손절없음(반대막돌파만)


def is_weekend():
    return datetime.now(KST).weekday() >= 5
TAKER = 0.0005
STATE = f"paper_state_MACD_{SYMBOL}.json"
TLOG = f"trades_MACD_{SYMBOL}.jsonl"
CFG = {"atr_period": 14, "rci_long": 26, "chikou_shift": 26, "pivot_left": 3,
       "pivot_right": 3, "trend_pivot": 8, "rem_req": 3, "atr_stop_mult": 2.0,
       "limit_offset": 0.0003, "trend_lookback": 100, "fresh_bars": 3}


def load():
    if os.path.exists(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except Exception:
            pass
    return None


def save(st):
    if st is None:
        if os.path.exists(STATE):
            os.remove(STATE)
    else:
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def mark_price():
    d = data.get_history(SYMBOL, "1m", bars=3)
    return float(d["close"].iloc[-1])


def signals():
    d10 = data.get_history(SYMBOL, "10m", bars=600)
    d30 = data.get_history(SYMBOL, "30m", bars=300)
    d1h = data.get_history(SYMBOL, "1h", bars=200)
    d2h = data.get_history(SYMBOL, "2h", bars=200)
    sig = strategy.build_signals(d10, d30, d1h, d2h, CFG)
    ema12 = d10["close"].ewm(span=12, adjust=False).mean()
    ema26 = d10["close"].ewm(span=26, adjust=False).mean()
    macd = float((ema12 - ema26).iloc[-2])           # 마지막 마감봉 MACD라인
    # 막돌파 = (long_all & fresh≥3)의 '새로 참됨' 에지 — Pine·무필터봇과 동일
    makL_s = sig["long_all"].astype(bool) & (sig["fresh_long"] >= 3)
    makS_s = sig["short_all"].astype(bool) & (sig["fresh_short"] >= 3)
    mak_long = bool((makL_s & ~makL_s.shift(1, fill_value=False)).iloc[-2])
    mak_short = bool((makS_s & ~makS_s.shift(1, fill_value=False)).iloc[-2])
    r = sig.iloc[-2]
    bar = sig.index[-2]
    return bar, mak_long, mak_short, r["swing_low"], r["swing_high"], r["atr"], macd


def _swing(direction, entry, swl, swh, atr):
    sw = swl if direction == "long" else swh
    d = 1 if direction == "long" else -1
    if (sw != sw) or (direction == "long" and sw >= entry) or (direction == "short" and sw <= entry):
        sw = entry - atr * CFG["atr_stop_mult"] * d
    return sw


def enter(direction, price, swl, swh, atr, macd):
    qty = round((MARGIN * LEV) / price, 3)
    st = {"dir": direction, "entry": price, "qty": qty,
          "swing": _swing(direction, price, swl, swh, atr),
          "opened": datetime.now(KST).isoformat(), "macd_in": round(macd, 2)}
    save(st)
    log.info("[페이퍼] 진입 %s qty=%s entry=%.2f 손절=%.2f (MACD=%.1f)", direction, qty, price, st["swing"], macd)
    return st


def close(st, price, reason):
    d = 1 if st["dir"] == "long" else -1
    gross = (price - st["entry"]) * d * st["qty"]
    fee = (st["entry"] + price) * st["qty"] * TAKER
    pnl = gross - fee
    rec = {"dir": st["dir"], "entry": st["entry"], "exit": round(price, 2), "qty": st["qty"],
           "opened": st.get("opened"), "closed": datetime.now(KST).isoformat(),
           "closed_ms": int(time.time() * 1000), "reason": reason,
           "pnl": round(pnl, 3), "meta": {"macd_in": st.get("macd_in")}}
    with open(TLOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    log.info("[페이퍼] 청산 %s @%.2f pnl=%.2f (%s)", st["dir"], price, pnl, reason)
    return pnl


def main():
    log.info("페이퍼 MACD필터 봇 시작: %s | 증거금 %s×%sx | 홀드+SAR | MACD0선 '대기(deferred)' | 주말스킵 %s",
             SYMBOL, MARGIN, LEV, WEEKEND_OFF)
    st = load()
    pend = None          # {"dir": 1/-1}: 막돌파 떴으나 MACD 반대편 → 0선돌파 대기중
    last_bar = None
    while True:
        try:
            px = mark_price()
            # 손절 감시 — USE_STOP=False면 스킵(반대막돌파로만 청산)
            if st is not None and USE_STOP:
                is_long = st["dir"] == "long"
                if (px <= st["swing"]) if is_long else (px >= st["swing"]):
                    close(st, px, "손절"); st = None; save(st)
            # 새 봉 마감 시 신호 처리
            bar, mak_long, mak_short, swl, swh, atr, macd = signals()
            if bar != last_bar:
                last_bar = bar
                # ── 신호 판정: 막돌파+MACD정렬 즉시 / 또는 막돌파 후 MACD 0선돌파 '대기' ──
                sig_dir = None   # +1 롱, -1 숏
                if mak_long:
                    if macd > 0:
                        sig_dir = 1; pend = None
                    else:
                        pend = {"dir": 1}                 # 매수막돌파 예약(MACD 0선위 대기)
                elif mak_short:
                    if macd < 0:
                        sig_dir = -1; pend = None
                    else:
                        pend = {"dir": -1}                # 매도막돌파 예약(MACD 0선아래 대기)
                elif pend is not None:                    # 대기중 → MACD 0선 정렬되면 발동
                    if pend["dir"] == 1 and macd > 0:
                        sig_dir = 1; pend = None
                    elif pend["dir"] == -1 and macd < 0:
                        sig_dir = -1; pend = None
                # ── 신호 실행 (홀드+SAR, 주말 신규진입 스킵) ──
                if sig_dir is not None:
                    want = "long" if sig_dir == 1 else "short"
                    weekend = WEEKEND_OFF and is_weekend()
                    if st is None:
                        if not weekend:
                            st = enter(want, px, swl, swh, atr, macd)
                    elif st["dir"] != want:               # 반대 → 청산+SAR
                        close(st, px, "반대막돌파"); st = None
                        if not weekend:
                            st = enter(want, px, swl, swh, atr, macd)
            save(st)
        except Exception as e:
            log.warning("[페이퍼] 루프 오류: %s", e)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
