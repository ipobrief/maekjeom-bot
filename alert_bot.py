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
    "pivot_left": 3, "pivot_right": 3,    # 손절용 직전저점/고점
    "trend_pivot": 8,                # 대각선용 스윙 강도(8=주요 스윙, 큰 그림 추세선)
    "rem_req": 4,                    # 필수2(선행스팬1·20일선) 외 나머지7 중 4개
    "atr_stop_mult": 2.0,
    "limit_offset": 0.0003,          # 지정가 진입 = 현재가 ±0.03% (1호가 아래/위)
}
SYMBOL = "BTCUSDT"
TF = "1h"          # 주력 타임프레임 (1시간봉이 백테스트상 구조적으로 우월)
# 상위 타임프레임(방향 필터)과 표시 라벨 — 주력 TF에 맞춰 조정
HTF = ("2h", "4h", "1d")
HTF_LABELS = ("2시간", "4시간", "일봉")


def tg_html(text):
    """<b>/<i> 태그는 살리고 나머지 <,>,& 는 이스케이프 (텔레그램 HTML parse_mode용).
    카드 본문에 '종가 > 선행스팬1' 같은 부등호가 있어 그대로 보내면 파싱이 깨짐."""
    t = (text.replace("<b>", "\x01").replace("</b>", "\x02")
             .replace("<i>", "\x03").replace("</i>", "\x04"))
    t = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return (t.replace("\x01", "<b>").replace("\x02", "</b>")
             .replace("\x03", "<i>").replace("\x04", "</i>"))


def tg_send(text):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("⚠️ 텔레그램 미설정: TELEGRAM_TOKEN / TELEGRAM_CHAT_ID 환경변수가 비어 있습니다 (콘솔만 출력).")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat, "text": tg_html(text),
                                     "parse_mode": "HTML"}, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램 전송 성공")
            return True
        # HTML 파싱 실패 시 태그 제거 평문으로 폴백(알림 유실 방지)
        print(f"⚠️ HTML 전송 실패({j.get('description')}) → 평문 재시도")
        clean = text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
        r = requests.post(url, data={"chat_id": chat, "text": clean}, timeout=10)
        j = r.json()
        if j.get("ok"):
            print("✅ 텔레그램 전송 성공(평문)")
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


def snapshot(live=False):
    """현재 데이터로 신호 계산. 주력=15분봉, 상위TF=1h/4h/1d.
    live=False → 마지막 마감봉(-2) 기준(확정).
    live=True  → 형성 중 봉(-1) 기준(현재가를 종가로 보고 마감 전 잠정 판정)."""
    df15 = data.get_history(SYMBOL, TF, bars=600)
    df1h = data.get_history(SYMBOL, HTF[0], bars=400)
    df4h = data.get_history(SYMBOL, HTF[1], bars=300)
    df1d = data.get_history(SYMBOL, HTF[2], bars=200)
    sig = strategy.build_signals(df15, df1h, df4h, df1d, CFG)
    idx = -1 if live else -2
    return sig.iloc[idx], sig.index[idx], sig


def enrich(closed, sig):
    """explain (대각선 res_line/sup_line·ma20 포함)."""
    return strategy.explain(closed, CFG)


def fmt_checks(checks):
    return "\n".join(f"  {'✅' if v else '❌'} {k}" for k, v in checks.items())


def fmt_signal(e, when, provisional=False, mins_left=None, active_dir=None):
    d = active_dir if active_dir is not None else e["direction"]
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
    # 익절 참고선(대각선): 롱=상승대각선(sup) 하향이탈 / 숏=하락대각선(res) 상향돌파
    # 단, exit_line이 진입가보다 수익 방향에 있어야 유효 (롱: sup < px / 숏: res < px)
    if provisional:
        left = f"마감 {mins_left:.0f}분 전" if mins_left is not None else "마감 전"
        head = (f"<b>⚡ {side} 예비신호 (잠정)</b> — {SYMBOL} ({TF})\n"
                f"⏱ {kst(when):%Y-%m-%d %H:%M} KST 봉 형성중 · {left}\n"
                f"⚠️ <b>마감 전 현재가 기준</b> — 봉 마감까지 되돌리면 취소될 수 있음\n")
    else:
        head = (f"<b>{side} 진입신호</b> — {SYMBOL} ({TF})\n"
                f"⏱ {kst(when):%Y-%m-%d %H:%M} KST ({TF} 마감)\n")
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
        f"📐 진입 전 <b>가로 매물대·채널·피보나치</b> 반드시 작도 후 최종 결정!\n"
        f"<i>판독이지 매매권유 아님. 최종 판단은 본인.</i>"
    )


def fmt_status(e, when):
    """신호가 없을 때도 현재 상태를 토론용으로 출력 (조건 충족도)."""
    cl, cs = e["checks_long"], e["checks_short"]
    return (
        f"📋 <b>{SYMBOL} 진단</b> {kst(when):%H:%M} KST 마감\n"
        f"💵 {e['close']:,.1f} / 선행스팬1 {e['senkou1']:,.0f} / 20일선 {e['ma20']:,.0f}\n"
        f"상위TF — {HTF_LABELS[0]} {e['tf_1h']} / {HTF_LABELS[1]} {e['tf_4h']} / {HTF_LABELS[2]} {e['tf_1d']}\n"
        f"<b>롱</b> 필수 {sum(e['must_long'].values())}/2 · 나머지 {sum(e['rem_long'].values())}/6\n{fmt_checks(cl)}\n"
        f"<b>숏</b> 필수 {sum(e['must_short'].values())}/2 · 나머지 {sum(e['rem_short'].values())}/6\n{fmt_checks(cs)}"
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


def run_watch(poll_sec=20):
    """봉 마감을 기다리지 않고, 형성 중 봉의 현재가로 실시간 판정.
    현재가가 조건(대각선 이탈 등)을 막 충족하는 '순간' 예비신호(잠정)를 1회 발송.
    같은 형성봉에서는 중복 발송하지 않으며, 봉이 바뀌면 다시 감시한다.
    확정 신호는 기존 --cron/--live가 봉 마감 때 별도로 보낸다."""
    emit(f"⚡ 맥점 실시간 감시 시작 — {SYMBOL} {TF} (현재가 기준, {poll_sec}초 간격)"
         + ("" if os.environ.get("TELEGRAM_TOKEN") else " (콘솔 모드)"))
    alerted_bar = None    # 예비신호 추적 중인 형성봉 시각
    alerted_dirs = set()  # 이 형성봉에서 이미 알린 방향들(중복 방지)
    while True:
        try:
            row, when, sig = snapshot(live=True)
            e = enrich(row, sig)
            if when != alerted_bar:           # 새 형성봉이면 이력 초기화
                alerted_bar = when
                alerted_dirs = set()
            d = e["direction"]
            # 같은 봉·같은 방향은 한 번만. 되돌림 메시지는 보내지 않음.
            if d and d not in alerted_dirs:
                now = pd.Timestamp.now(tz="UTC")
                mins_left = (when + pd.Timedelta(TF) - now).total_seconds() / 60
                emit(fmt_signal(e, when, provisional=True, mins_left=max(0, mins_left)))
                alerted_dirs.add(d)
        except Exception as ex:
            print("실시간 점검 오류:", ex)
        time.sleep(poll_sec)


def run_once():
    closed, when, sig = snapshot()
    e = enrich(closed, sig)
    if e["direction"]:
        emit(fmt_signal(e, when))
    else:
        emit(fmt_status(e, when))


def run_test_message():
    """실제 신호와 무관하게 샘플 알림을 강제 발송(텔레그램 경로 점검용)."""
    closed, when, sig = snapshot()
    e = enrich(closed, sig)
    if not e["direction"]:   # 신호 없으면 더 충족된 쪽으로 샘플 구성
        e["direction"] = ("LONG" if sum(e["checks_long"].values()) >= sum(e["checks_short"].values())
                          else "SHORT")
    emit("🧪 <b>[테스트 발송 — 실제 진입신호 아님]</b>\n" + fmt_signal(e, when))


def run_cron():
    """클라우드(GitHub Actions)용: 방금 마감된 봉에 신호가 있을 때만 알림.
    매시간 호출되며, 마지막 마감봉이 '신선'할 때만 전송해 중복을 막는다.
    저장소에 TESTSEND 파일이 있으면 1회 테스트 발송."""
    if os.path.exists("TESTSEND"):
        run_test_message()
        return
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
    elif "--test" in sys.argv:
        run_test_message()
    elif "--cron" in sys.argv:
        run_cron()
    elif "--watch" in sys.argv:
        sec = 20
        for a in sys.argv:
            if a.startswith("--poll="):
                sec = int(a.split("=", 1)[1])
        run_watch(sec)
    else:
        run_live()
