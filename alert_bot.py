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
import indicators as ind

KST = ZoneInfo("Asia/Seoul")


def trendline_level(sig, kind, cfg):
    """최근 스윙 고점(res)/저점(sup) 2개를 이어 대각선 추세선을 근사,
    현재 봉 위치로 연장한 레벨을 반환. (눈으로 긋는 추세선의 근사치)"""
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


def kst(ts):
    """UTC 타임스탬프를 한국시간으로 변환."""
    return ts.tz_convert(KST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(KST)

# 선행스팬1 돌파 추세추종 규칙 (사용자 정의)
# 백테스트 결론: 1시간봉 + 트레일링 청산 + 양방향이 최적. 손절=직전저점/고점.
CFG = {
    "atr_period": 14,
    "rci_long": 26,
    "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3,
    "atr_stop_mult": 2.0,            # 직전저점/고점 없을 때 대체
    "require_confirms": 4,           # 선행스팬1 돌파 + 보조 4/4 (진입 근거 5개 전부)
    "exit_mode": "loose",
    "limit_offset": 0.0003,          # 지정가 진입 = 현재가 ±0.03% (1호가 아래/위)
    "trend_lookback": 80,            # 대각선 추세선 근사용 최근 봉 수
}
SYMBOL = "BTCUSDT"
TF = "15m"         # 주력 타임프레임 (진입 판단은 사용자가)


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
    """현재 데이터로 신호 계산. 주력=15분봉, 상위TF=1h/4h/1d. 마지막 마감봉(-2) 기준."""
    df15 = data.get_history(SYMBOL, TF, bars=600)
    df1h = data.get_history(SYMBOL, "1h", bars=400)
    df4h = data.get_history(SYMBOL, "4h", bars=300)
    df1d = data.get_history(SYMBOL, "1d", bars=300)
    sig = strategy.build_signals(df15, df1h, df4h, df1d, CFG)
    closed = sig.iloc[-2]            # 마지막 완성봉
    closed_time = sig.index[-2]
    return closed, closed_time, sig


def enrich(closed, sig):
    """explain + 대각선 추세선(익절 참고선) 레벨 부착."""
    e = strategy.explain(closed, CFG)
    e["res_line"] = trendline_level(sig.iloc[:-1], "res", CFG)   # 하락 추세선(숏 익절 기준)
    e["sup_line"] = trendline_level(sig.iloc[:-1], "sup", CFG)   # 상승 추세선(롱 익절 기준)
    return e


def fmt_checks(checks):
    return "\n".join(f"  {'✅' if v else '❌'} {k}" for k, v in checks.items())


def fmt_signal(e, when):
    d = e["direction"]
    long_ = d == "LONG"
    side = "🟢 롱(LONG)" if long_ else "🔴 숏(SHORT)"
    px = e["close"]
    # 지정가 진입 권장 (1호가 아래/위)
    limit = px * (1 - CFG["limit_offset"]) if long_ else px * (1 + CFG["limit_offset"])
    # 손절선 = 직전저점(롱)/직전고점(숏), 없으면 ATR 대체
    swing = e["swing_low"] if long_ else e["swing_high"]
    bad = (swing != swing) or (long_ and swing >= px) or (not long_ and swing <= px)
    if bad:
        swing = px - e["atr"] * CFG["atr_stop_mult"] * (1 if long_ else -1)
        sl_txt = f"{swing:,.1f} (직전저저점 불명확→ATR 대체)"
    else:
        sl_txt = f"{swing:,.1f} ({'직전저점' if long_ else '직전고점'})"
    risk_pct = abs(px - swing) / px * 100
    checks = e["checks_long"] if long_ else e["checks_short"]
    aligned = (e["bias"] > 0) == long_ and abs(e["bias"]) >= 2
    # 익절 참고선(대각선 추세선): 롱=상승추세선 하향이탈 / 숏=하락추세선 상향돌파
    exit_line = e["sup_line"] if long_ else e["res_line"]
    exit_txt = (f"{exit_line:,.1f} {'하향이탈' if long_ else '상향돌파'} 시 (직접 판단)"
                if exit_line else "대각선 추세선 돌파 시 (직접 판단)")
    return (
        f"<b>{side} 진입신호</b> — {SYMBOL} ({TF})\n"
        f"⏱ {kst(when):%Y-%m-%d %H:%M} KST ({TF} 마감)\n"
        f"📊 <b>상위TF 방향</b> {'✅추세정렬' if aligned else '⚠️역추세—신중'}\n"
        f"   · 1시간 {e['tf_1h']}\n"
        f"   · 4시간 {e['tf_4h']}\n"
        f"   · 일봉  {e['tf_1d']}\n"
        f"━━━━━━━━━━━━━\n"
        f"💵 현재가 {px:,.1f}\n"
        f"📥 지정가 진입 {limit:,.1f} (1호가 {'아래' if long_ else '위'})\n"
        f"🛑 손절 {sl_txt} → 리스크 {risk_pct:.2f}%\n"
        f"🎯 익절(시장가): 대각선 추세선 {exit_txt}\n"
        f"━━━━━━━━━━━━━\n"
        f"<b>진입 근거 체크리스트 (5/5)</b>\n{fmt_checks(checks)}\n"
        f"ℹ️ 선행스팬1 {e['senkou1']:,.0f} / 스토%K {e['k']:.0f} / RCI {e['rci_long']:.0f}\n"
        f"<i>판독이지 매매권유 아님. 진입=지정가/익절=시장가. 최종 판단은 본인.</i>"
    )


def fmt_status(e, when):
    """신호가 없을 때도 현재 상태를 토론용으로 출력 (조건 충족도)."""
    cl, cs = e["checks_long"], e["checks_short"]
    return (
        f"📋 <b>{SYMBOL} 진단</b> {kst(when):%H:%M} KST 마감\n"
        f"💵 {e['close']:,.1f} / 선행스팬1 {e['senkou1']:,.0f} / 스토%K {e['k']:.0f} / RCI {e['rci_long']:.0f}\n"
        f"상위TF — 1h {e['tf_1h']} / 4h {e['tf_4h']} / 일봉 {e['tf_1d']}\n"
        f"<b>롱 조건</b> ({sum(cl.values())}/5)\n{fmt_checks(cl)}\n"
        f"<b>숏 조건</b> ({sum(cs.values())}/5)\n{fmt_checks(cs)}"
    )


def check_and_alert(last_alerted):
    closed, when, sig = snapshot()
    e = enrich(closed, sig)
    if e["direction"] and when != last_alerted:
        emit(fmt_signal(e, when))
        return when
    return last_alerted


def run_live():
    emit(f"🤖 맥점 알림봇 시작 — {SYMBOL} {TF} 감시 중"
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
    closed, when, sig = snapshot()
    e = enrich(closed, sig)
    if e["direction"]:
        emit(fmt_signal(e, when))
    else:
        emit(fmt_status(e, when))


def run_cron():
    """클라우드(GitHub Actions)용: 방금 마감된 봉에 신호가 있을 때만 알림.
    매시간 호출되며, 마지막 마감봉이 '신선'할 때만 전송해 중복을 막는다."""
    closed, when, sig = snapshot()
    closed_bar_close = sig.index[-1]                 # 마지막 마감봉의 종료시각(=형성중 봉 시작)
    now = pd.Timestamp.now(tz="UTC")
    age_min = (now - closed_bar_close).total_seconds() / 60
    e = enrich(closed, sig)
    if e["direction"] and age_min <= 16:             # 15분봉 + 지연 여유
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
