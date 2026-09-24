# -*- coding: utf-8 -*-
"""
트랙2 추세매매 실행기 (BTCUSDT 선물) — B설정: 본절런너.

자금관리(백테스트 검증 완료: 10분 6개월 PF 2.58/승률 74%):
  · 진입: (forward_runner가) 10분 눌림목 신호(막돌파+MACD정렬)로 enter() 호출
  · 초기손절: 전저점(롱)/전고점(숏)
  · +0.3% 유리해지면 → 손절을 본절(수수료 포함 무손실)로 이동
  · 청산: 반대신호(매도맥점/매수맥점) 시 exit_now(), 또는 손절/본절 히트
  · 부분익절 없음(전량 러너), 주말(KST) 신규진입 스킵

⚠️ 손절/익절은 '봇 관리'(check가 마크가격 감시→시장가). 봇 다운 중엔 무방비.
   실계좌 전 거래소 Algo 손절 백업 추가 예정. 기본 테스트넷. 단방향 모드 가정.
"""
import os
import json
import time
import logging
from datetime import datetime, timezone, timedelta

from binance_futures import BinanceFutures

log = logging.getLogger("trend_executor")
KST = timezone(timedelta(hours=9))


def _f(k, d):
    try:
        return float(os.environ.get(k, d))
    except ValueError:
        return d


CFG = {
    "symbol": os.environ.get("SYMBOL", "BTCUSDT"),
    "leverage": int(_f("LEVERAGE", 10)),
    "margin_per_trade": _f("MARGIN_PER_TRADE", 100.0),  # USDT (미정 — 데모 기본)
    "be_after": _f("BE_AFTER", 0.003),   # +0.3% 유리 시 본절 이동
    "be_buffer": _f("BE_BUFFER", 0.0012),  # 본절 수수료버퍼
    "weekend_off": os.environ.get("WEEKEND_OFF", "1").lower() not in ("0", "false", "no"),
    "use_stop": os.environ.get("USE_STOP", "1").lower() not in ("0", "false", "no"),  # 0=손절없음(반대막돌파만 청산)
}


def is_weekend_kst():
    return datetime.now(KST).weekday() >= 5


class TrendExecutor:
    def __init__(self, ex=None, cfg=None):
        self.ex = ex or BinanceFutures()
        self.cfg = dict(CFG, **(cfg or {}))
        self.symbol = self.cfg["symbol"]
        self.state_file = f"trend_state_{self.symbol}.json"
        self.trades_file = f"trades_{self.symbol}.jsonl"
        self.state = self._load()

    def _load(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _save(self):
        if self.state is None:
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
        else:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _reset(self):
        self.state = None
        self._save()

    def _record_close(self, exit_px, reason, qty):
        """청산 시 거래기록 1줄 append (진입·청산시각 포함). PnL은 forward_stats가
        income_history를 closed_ms로 매칭해 채움(수수료 정확)."""
        if not self.state:
            return
        rec = {
            "dir": self.state["dir"],
            "entry": self.state["entry"],
            "exit": round(exit_px, 2) if exit_px else None,
            "qty": qty,
            "opened": self.state.get("opened"),
            "closed": datetime.now(KST).isoformat(),
            "closed_ms": int(time.time() * 1000),
            "reason": reason,
            "meta": self.state.get("meta", {}),
        }
        try:
            with open(self.trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log.warning("거래기록 저장 실패: %s", e)

    def position_amt(self):
        return self.ex.position(self.symbol)["amt"]

    # ── 진입 ──────────────────────────────────────────────────────────────────
    def enter(self, direction, swing_sl, meta=None):
        if self.cfg["weekend_off"] and is_weekend_kst():
            log.info("주말(KST) — 진입 스킵"); return None
        if abs(self.position_amt()) > 0 or self.state is not None:
            log.info("이미 포지션 있음 — 진입 스킵"); return None
        lev = self.cfg["leverage"]
        self.ex.set_isolated(self.symbol)
        self.ex.set_leverage(self.symbol, lev)
        price = self.ex.mark_price(self.symbol)
        is_long = direction == "long"
        qty = self.ex.round_qty(self.symbol, (self.cfg["margin_per_trade"] * lev) / price)
        self.ex.market_order(self.symbol, "BUY" if is_long else "SELL", qty)
        pos = self.ex.position(self.symbol)
        entry = pos["entry"] or price
        if (is_long and swing_sl >= entry) or (not is_long and swing_sl <= entry):
            log.warning("손절이 진입 대비 역방향 — 즉시 청산")
            self.ex.market_order(self.symbol, "SELL" if is_long else "BUY", abs(pos["amt"]), reduce_only=True)
            return None
        self.state = {"dir": direction, "entry": entry, "swing_sl": swing_sl,
                      "peak": entry, "trough": entry, "be_active": False,
                      "opened": datetime.now(KST).isoformat(),
                      "meta": meta or {}}
        self._save()
        be_txt = ("+%.1f%%유리시 본절" % (self.cfg["be_after"] * 100)) if self.cfg["be_after"] > 0 else "본절없음(반대막돌파까지 홀드)"
        log.info("진입 %s qty=%s entry=%.2f 손절=%.2f (%s)",
                 direction, qty, entry, swing_sl, be_txt)
        return self.state

    # ── 감시: 본절 이동 + 손절 히트 ───────────────────────────────────────────
    def check(self):
        if self.state is None:
            return
        amt = self.position_amt()
        if amt == 0:
            log.info("포지션 없음 감지 — 리셋"); self._reset(); return
        is_long = self.state["dir"] == "long"
        entry = self.state["entry"]
        price = self.ex.mark_price(self.symbol)
        self.state["peak"] = max(self.state["peak"], price)
        self.state["trough"] = min(self.state["trough"], price)
        # 본절 활성화 (be_after<=0 이면 본절 이동 안 함 — 초기손절만, 반대막돌파까지 홀드)
        if self.cfg["be_after"] > 0 and not self.state["be_active"]:
            fav = (self.state["peak"] >= entry * (1 + self.cfg["be_after"])) if is_long \
                else (self.state["trough"] <= entry * (1 - self.cfg["be_after"]))
            if fav:
                self.state["be_active"] = True
                self._save()
                log.info("+%.1f%% 유리 도달 → 손절을 본절로 이동", self.cfg["be_after"] * 100)
        # 손절/본절 청산 — use_stop=False면 스킵(반대막돌파로만 청산)
        if self.cfg.get("use_stop", True) or self.state["be_active"]:
            if self.state["be_active"]:
                stop = entry * (1 + self.cfg["be_buffer"]) if is_long else entry * (1 - self.cfg["be_buffer"])
            else:
                stop = self.state["swing_sl"]
            if (price <= stop) if is_long else (price >= stop):
                self.ex.market_order(self.symbol, "SELL" if is_long else "BUY", abs(amt), reduce_only=True)
                reason = "본절" if self.state["be_active"] else "손절"
                log.info("%s 히트 @%.2f (stop=%.2f) → 청산", reason, price, stop)
                self._record_close(price, reason, abs(amt))
                self._reset()

    def exit_now(self, reason="opposite_signal"):
        amt = self.position_amt()
        exit_px = self.ex.mark_price(self.symbol) if amt != 0 else None
        if amt != 0:
            self.ex.market_order(self.symbol, "SELL" if amt > 0 else "BUY", abs(amt), reduce_only=True)
            self._record_close(exit_px, reason, abs(amt))
        log.info("청산(%s) 완료", reason)
        self._reset()

    def run(self, poll=20):
        log.info("감시 시작(poll=%ss) — Ctrl+C 중단", poll)
        while self.state is not None:
            try:
                self.check()
            except Exception as e:
                log.warning("check 오류: %s", e)
            time.sleep(poll)
        log.info("포지션 종료 — 감시 끝")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ex = TrendExecutor()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    sym = ex.symbol
    if cmd == "status":
        p = ex.ex.position(sym)
        print(f"모드: {'테스트넷' if ex.ex.testnet else '★실계좌★'} | {sym}")
        print(f"포지션 amt={p['amt']} entry={p['entry']} uPnL={p['unrealized']}")
        print(f"저장상태: {ex.state}")
        print(f"설정: {ex.cfg['leverage']}x, 증거금 {ex.cfg['margin_per_trade']}, "
              f"본절이동 +{ex.cfg['be_after']*100:.1f}%, 주말스킵 {ex.cfg['weekend_off']}")
    elif cmd in ("long", "short"):
        price = ex.ex.mark_price(sym)
        sl = price * (0.99 if cmd == "long" else 1.01)
        ex.enter(cmd, sl)
    elif cmd == "check":
        ex.check(); print("상태:", ex.state)
    elif cmd == "run":
        ex.run()
    elif cmd == "close":
        ex.exit_now("manual"); print("청산 완료")
    else:
        print("사용법: python trend_executor.py [status|long|short|check|run|close]")
