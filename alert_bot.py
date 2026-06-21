# -*- coding: utf-8 -*-
"""맥점 알림봇 — 15분봉 마감마다 신호를 점검하고 '근거'까지 풀어서 알림.
실주문 없음. 텔레그램 설정(환경변수)이 있으면 전송, 없으면 콘솔 출력.

환경변수(선택):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
실행:
  python alert_bot.py            # 라이브 감시(15분봉 마감마다)
  python alert_bot.py --once     # 현재 시장 1회 진단(토론용)
"""
import os
import sys
import time
import datetime as dt
from zoneinfo import ZoneInfo
import requests
import pandas as pd

import data
import strategy

KST = ZoneInfo("Asia/Seoul")


def kst(ts):
    """UTC 타임스탬프를 한국시간으로 변환."""
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)

# sweep.py에서 가장 나았던 설정(과매도심화+추세순응)을 기본값으로
CFG = {
    "atr_period": 14, "stoch_os": 15, "stoch_ob": 85,
    "pivot_left": 3, "pivot_right": 3, "level_tol": 0.004,
    "bias_long_min": 2.0, "bias_short_max": -2.0,
    "atr_stop_mult": 1.5, "rr_trend": 2.0, "rr_counter": 1.5,
}
SYMBOL = "BTCUSDT"


def tg_send(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("⚠️ 텔레그램 미설정: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어 있습니다 (콘솔만 출력).")
        return False
    clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": clean}, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램 전송 성공")
            return True
        print(f"❌ 텔레그램 전송 실패: {j.get('description')}")
        return False
    except Exception as e:
        print("❌ 텔레그램 전송 오류:", e)
        return False


def emit(text):
    clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(clean)
    tg_send(text)


def snapshot():
    """현재 데이터로 신호 컴포넌트 계산. 마지막 '마감된' 봉(-2)을 기준으로 본다."""
    df15 = data.get_history(SYMBOL, "15m", bars=600)
    df1h = data.get_history(SYMBOL, "1h", bars=400)
    df4h = data.get_history(SYMBOL, "4h", bars=300)
    df1d = data.get_history(SYMBOL, "1d", bars=300)
    sig = strategy.build_signals(df15, df1h, df4h, df1d, CFG)
    closed = sig.iloc[-2]            # 마지막 완성봉
    closed_time = sig.index[-2]
    return closed, closed_time, sig


def fmt_checks(checks):
    return "\n".join(f"  {'✅' if v else '❌'} {k}" for k, v in checks.items())


def fmt_signal(e, when):
    d = e["direction"]
    side = "🟢 롱(LONG)" if d == "LONG" else "🔴 숏(SHORT)"
    stop_dist = e["atr"] * CFG["atr_stop_mult"]
    if d == "LONG":
        sl = e["close"] - stop_dist
        tp2 = e["close"] + stop_dist * CFG["rr_trend"]
        tp1 = e["close"] + stop_dist * CFG["rr_counter"]
        checks = e["checks_long"]
    else:
        sl = e["close"] + stop_dist
        tp2 = e["close"] - stop_dist * CFG["rr_trend"]
        tp1 = e["close"] - stop_dist * CFG["rr_counter"]
        checks = e["checks_short"]
    aligned = abs(e["bias"]) >= 2 and ((e["bias"] > 0) == (d == "LONG"))
    return (
        f"<b>{side} 신호</b> — {SYMBOL}\n"
        f"⏱ {kst(when):%Y-%m-%d %H:%M} KST (15m 마감)\n"
        f"💵 진입가 {e['close']:,.1f}\n"
        f"🛑 손절 {sl:,.1f}  (ATR {e['atr']:,.1f} × {CFG['atr_stop_mult']})\n"
        f"🎯 익절 {tp1:,.1f}(1.5R) / {tp2:,.1f}(2R)\n"
        f"📊 상위TF: {e['bias_txt']} (bias {e['bias']:+.1f}) "
        f"→ {'추세순응(2R 권장)' if aligned else '역추세(짧게 1.5R)'}\n"
        f"<b>근거 체크</b>\n{fmt_checks(checks)}\n"
        f"ℹ️ 구름 {e['cloud_bot']:,.0f}~{e['cloud_top']:,.0f} / "
        f"직전고 {e['swing_high']:,.0f} 저 {e['swing_low']:,.0f} / "
        f"스토%K {e['k']:.0f}\n"
        f"<i>판독이지 매매권유 아님. 1분봉 직전고/저 돌파로 최종 확정.</i>"
    )


def fmt_status(e, when):
    """신호가 없을 때도 현재 상태를 토론용으로 출력."""
    cl, cs = e["checks_long"], e["checks_short"]
    return (
        f"📋 <b>{SYMBOL} 진단</b> {kst(when):%H:%M} KST 마감\n"
        f"💵 {e['close']:,.1f} / 상위TF {e['bias_txt']}({e['bias']:+.1f}) / 스토%K {e['k']:.0f}\n"
        f"구름 {e['cloud_bot']:,.0f}~{e['cloud_top']:,.0f} / 직전고 {e['swing_high']:,.0f} 저 {e['swing_low']:,.0f}\n"
        f"<b>롱 조건</b> ({sum(cl.values())}/4)\n{fmt_checks(cl)}\n"
        f"<b>숏 조건</b> ({sum(cs.values())}/4)\n{fmt_checks(cs)}"
    )


def check_and_alert(last_alerted):
    closed, when, _ = snapshot()
    e = strategy.explain(closed, CFG)
    if e["direction"] and when != last_alerted:
        emit(fmt_signal(e, when))
        return when
    return last_alerted


def run_live():
    emit(f"🤖 맥점 알림봇 시작 — {SYMBOL} 15분봉 감시 중"
         + ("" if os.environ.get("TELEGRAM_TOKEN") else " (콘솔 모드: 텔레그램 미설정)"))
    last_alerted = None
    while True:
        try:
            last_alerted = check_and_alert(last_alerted)
        except Exception as ex:
            print("점검 오류:", ex)
        # 다음 15분 경계 + 5초 후 깨어남
        now = dt.datetime.now(dt.timezone.utc)
        nxt = (now + dt.timedelta(minutes=15)).replace(second=5, microsecond=0)
        nxt = nxt.replace(minute=(nxt.minute // 15) * 15)
        if nxt <= now:
            nxt += dt.timedelta(minutes=15)
        time.sleep(max(10, (nxt - now).total_seconds()))


def run_once():
    closed, when, _ = snapshot()
    e = strategy.explain(closed, CFG)
    if e["direction"]:
        emit(fmt_signal(e, when))
    else:
        emit(fmt_status(e, when))


def run_cron():
    """클라우드(GitHub Actions)용: 방금 마감된 봉에 신호가 있을 때만 알림.
    15분마다 호출되며, 마지막 마감봉이 '신선'할 때만 전송해 중복을 막는다."""
    closed, when, sig = snapshot()
    closed_bar_close = sig.index[-1]                 # 마지막 마감봉의 종료시각(=형성중 봉 시작)
    now = pd.Timestamp.now(tz="UTC")
    age_min = (now - closed_bar_close).total_seconds() / 60
    e = strategy.explain(closed, CFG)
    if e["direction"] and age_min <= 16:
        emit(fmt_signal(e, when))
    else:
        # 알림 없이 로그만 (텔레그램 전송 안 함)
        tag = e["direction"] or "신호없음"
        print(f"[cron] {kst(when):%m-%d %H:%M} KST 점검: {tag} "
              f"(신선도 {age_min:.0f}분) — 알림 조건 미충족, 전송 생략")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    elif "--cron" in sys.argv:
        run_cron()
    else:
        run_live()
