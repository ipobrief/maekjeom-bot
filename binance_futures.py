# -*- coding: utf-8 -*-
"""
바이낸스 USDT-M 선물 REST 래퍼 (트랙2 추세매매 실행용).

⚠️ 안전 기본값: BINANCE_TESTNET 미설정/1 → 테스트넷(가짜돈).
   실계좌는 BINANCE_TESTNET=0 을 '명시적으로' 넣어야만 동작.

키는 코드에 절대 박지 않는다 — 환경변수로만:
  BINANCE_API_KEY, BINANCE_API_SECRET
  (선물 전용 키. '거래' 권한만, '출금' 권한 OFF, IP 화이트리스트 권장)

의존성: requests (requirements.txt에 이미 있음). ccxt 안 씀.
"""
import os
import time
import hmac
import hashlib
import logging
from urllib.parse import urlencode

import requests

log = logging.getLogger("binance_futures")

# ── 엔드포인트 ────────────────────────────────────────────────────────────────
_LIVE_BASE = "https://fapi.binance.com"
# 2026 개편: 구 testnet.binancefuture.com → 데모(demo-fapi.binance.com)
_TEST_BASE = "https://demo-fapi.binance.com"


def _is_testnet() -> bool:
    # 기본 테스트넷. 실계좌는 명시적으로 "0"/"false"/"no" 를 넣어야 함.
    return os.environ.get("BINANCE_TESTNET", "1").strip().lower() not in ("0", "false", "no")


class BinanceFutures:
    def __init__(self, api_key=None, api_secret=None, testnet=None, recv_window=5000, timeout=10):
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = (api_secret or os.environ.get("BINANCE_API_SECRET", "")).encode()
        self.testnet = _is_testnet() if testnet is None else testnet
        self.base = _TEST_BASE if self.testnet else _LIVE_BASE
        self.recv_window = recv_window
        self.timeout = timeout
        self._filters = {}  # symbol -> {stepSize, tickSize, minQty, minNotional}
        self._sess = requests.Session()
        if self.api_key:
            self._sess.headers.update({"X-MBX-APIKEY": self.api_key})
        log.info("BinanceFutures init: %s", "TESTNET" if self.testnet else "!!! LIVE 실계좌 !!!")

    # ── 저수준 요청 ──────────────────────────────────────────────────────────
    def _get(self, path, params=None, signed=False):
        return self._request("GET", path, params, signed)

    def _post(self, path, params=None, signed=True):
        return self._request("POST", path, params, signed)

    def _delete(self, path, params=None, signed=True):
        return self._request("DELETE", path, params, signed)

    def _request(self, method, path, params, signed):
        params = dict(params or {})
        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("API 키/시크릿 없음 — 환경변수 BINANCE_API_KEY/SECRET 설정 필요")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window
            query = urlencode(params)
            sig = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
            params["signature"] = sig
        url = self.base + path
        r = self._sess.request(method, url, params=params, timeout=self.timeout)
        if r.status_code >= 400:
            # 바이낸스 에러 본문을 그대로 노출(디버깅용). 시크릿은 포함 안 됨.
            raise RuntimeError(f"바이낸스 {method} {path} 실패 [{r.status_code}]: {r.text}")
        return r.json()

    # ── 심볼 필터(수량/가격 반올림) ──────────────────────────────────────────
    def _load_filters(self, symbol):
        if symbol in self._filters:
            return self._filters[symbol]
        info = self._get("/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s["symbol"] == symbol:
                f = {}
                for flt in s["filters"]:
                    if flt["filterType"] == "LOT_SIZE":
                        f["stepSize"] = float(flt["stepSize"])
                        f["minQty"] = float(flt["minQty"])
                    elif flt["filterType"] == "PRICE_FILTER":
                        f["tickSize"] = float(flt["tickSize"])
                    elif flt["filterType"] in ("MIN_NOTIONAL", "NOTIONAL"):
                        f["minNotional"] = float(flt.get("notional", flt.get("minNotional", 0)))
                f["qtyPrec"] = s.get("quantityPrecision", 3)
                f["pricePrec"] = s.get("pricePrecision", 2)
                self._filters[symbol] = f
                return f
        raise RuntimeError(f"심볼 {symbol} exchangeInfo에 없음")

    @staticmethod
    def _round_step(value, step):
        if step <= 0:
            return value
        return round(round(value / step) * step, 12)

    def round_qty(self, symbol, qty):
        f = self._load_filters(symbol)
        q = self._round_step(qty, f["stepSize"])
        return round(q, f["qtyPrec"])

    def round_price(self, symbol, price):
        f = self._load_filters(symbol)
        p = self._round_step(price, f["tickSize"])
        return round(p, f["pricePrec"])

    # ── 시세/계좌 조회 (읽기전용) ─────────────────────────────────────────────
    def mark_price(self, symbol):
        return float(self._get("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])

    def balance_usdt(self):
        for b in self._get("/fapi/v2/balance", signed=True):
            if b["asset"] == "USDT":
                return float(b["balance"]), float(b["availableBalance"])
        return 0.0, 0.0

    def position(self, symbol):
        """현재 포지션 하나(단일 포지션 모드 가정). 없으면 amt=0."""
        rows = self._get("/fapi/v2/positionRisk", {"symbol": symbol}, signed=True)
        for p in rows:
            if p["symbol"] == symbol:
                amt = float(p["positionAmt"])
                return {
                    "amt": amt,                       # +롱 / -숏 / 0 없음
                    "entry": float(p["entryPrice"]),
                    "unrealized": float(p["unRealizedProfit"]),
                    "leverage": float(p.get("leverage", 0)),
                }
        return {"amt": 0.0, "entry": 0.0, "unrealized": 0.0, "leverage": 0.0}

    # ── 설정/주문 ─────────────────────────────────────────────────────────────
    def set_leverage(self, symbol, leverage):
        return self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)})

    def set_isolated(self, symbol):
        try:
            return self._post("/fapi/v1/marginType", {"symbol": symbol, "marginType": "ISOLATED"})
        except RuntimeError as e:
            # 이미 ISOLATED이면 -4046 에러 → 무시
            if "-4046" in str(e):
                return {"msg": "already isolated"}
            raise

    def market_order(self, symbol, side, qty, reduce_only=False):
        """side: 'BUY' or 'SELL'. reduce_only=True면 청산 전용(반대로 못 뒤집음)."""
        qty = self.round_qty(symbol, abs(qty))
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": qty}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/order", params)

    def stop_market(self, symbol, side, stop_price, close_position=True, qty=None):
        """손절용 STOP_MARKET. 기본 closePosition=true(전량 청산).
        일부만 걸려면 close_position=False + qty + reduceOnly."""
        stop_price = self.round_price(symbol, stop_price)
        params = {"symbol": symbol, "side": side, "type": "STOP_MARKET",
                  "stopPrice": stop_price, "workingType": "MARK_PRICE"}
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = self.round_qty(symbol, abs(qty))
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/order", params)

    def take_profit_market(self, symbol, side, stop_price, qty=None, close_position=False):
        """익절용 TAKE_PROFIT_MARKET. 부분익절은 close_position=False + qty + reduceOnly."""
        stop_price = self.round_price(symbol, stop_price)
        params = {"symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
                  "stopPrice": stop_price, "workingType": "MARK_PRICE"}
        if close_position:
            params["closePosition"] = "true"
        else:
            params["quantity"] = self.round_qty(symbol, abs(qty))
            params["reduceOnly"] = "true"
        return self._post("/fapi/v1/order", params)

    def cancel_all(self, symbol):
        return self._delete("/fapi/v1/allOpenOrders", {"symbol": symbol})

    def income_history(self, symbol=None, income_type=None, limit=1000):
        """실현손익/수수료/펀딩 내역 (/fapi/v1/income). 성과 집계용."""
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        if income_type:
            params["incomeType"] = income_type
        return self._get("/fapi/v1/income", params, signed=True)


# ── 연결 자가진단 (읽기전용: 주문 안 넣음) ────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sym = os.environ.get("SYMBOL", "BTCUSDT")
    ex = BinanceFutures()
    print(f"모드: {'테스트넷(가짜돈)' if ex.testnet else '★ 실계좌 ★'}")
    print(f"{sym} 마크가격: {ex.mark_price(sym):,.2f}")
    try:
        bal, avail = ex.balance_usdt()
        print(f"USDT 잔고: {bal:,.2f} / 가용: {avail:,.2f}")
        pos = ex.position(sym)
        print(f"현재 포지션: amt={pos['amt']} entry={pos['entry']} uPnL={pos['unrealized']}")
        f = ex._load_filters(sym)
        print(f"필터: stepSize={f['stepSize']} tickSize={f['tickSize']} minQty={f['minQty']}")
        print("✅ 연결·인증 정상 (주문은 안 넣음).")
    except Exception as e:
        print(f"⚠️ 인증/조회 실패: {e}")
        print("→ BINANCE_API_KEY / BINANCE_API_SECRET 환경변수 확인 (테스트넷 키인지도).")
