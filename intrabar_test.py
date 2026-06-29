# -*- coding: utf-8 -*-
"""즉시신호(예비, 봉 마감 전 진입) vs 확정신호(봉 마감 종가 진입) 비교 백테스트.

봉 안 움직임이 필요하므로 5분봉을 '봉 중간 틱'으로 사용해 15분 형성봉을 재구성한다.
각 15분봉마다 5분 하위봉을 순서대로 누적해 형성봉(open=구간시작, high/low=누적, close=현재 5분 종가)을
만들고, build_signals로 그 형성봉의 신호를 평가 → 조건을 처음 충족하는 5분 시점에 '즉시진입'.

두 모드는 청산 규칙·수수료를 100% 동일하게 두고 진입 시점/가격만 다르게 한다.
청산: 직전저고점 손절(시장가) 또는 반대 셋업 형성(종가, 시장가). 진입=지정가(메이커).
"""
import sys
import numpy as np
import pandas as pd
import data
import strategy

CFG = {
    "atr_period": 14, "rci_long": 26, "chikou_shift": 26,
    "pivot_left": 3, "pivot_right": 3, "trend_pivot": 3, "rem_req": 3,
    "atr_stop_mult": 2.0, "risk_per_trade": 0.01,
    "maker_fee": 0.0002, "taker_fee": 0.0005, "exit_slip": 0.0003,
    "limit_offset": 0.0003,        # 지정가(메이커) 진입 = 신호가 ±0.03%
    "start_equity": 10000.0,
}
# 타임프레임 설정 (기본: 15분 주력 + 5분 형성봉). 1시간 검증 시 main에서 오버라이드.
PRIMARY = "15m"; SUB = "5m"; FLOOR = "15min"; HTF = ("1h", "4h", "1d")
SUBPER = 3                                                  # 주력봉당 하위봉 수
N_TEST = 2500                                              # 검증할 주력봉 수 (__main__에서 오버라이드)
WIN = 400                                                   # 지표 계산용 트레일링 윈도우


def detect_immediate(df15, df5, df1h, df4h, df1d, test_idx):
    """각 테스트 봉에서 5분 하위봉을 누적해 형성봉을 만들고, 신호가 처음 켜지는
    5분 시점의 (방향, 진입가)를 찾는다. 못 켜지면 (0, nan)."""
    # 하위봉을 부모 주력봉 구간(open_time floor)으로 묶기
    parent = df5.index.floor(FLOOR)
    groups = {ts: g for ts, g in df5.groupby(parent)}
    imm_dir = np.zeros(len(df15)); imm_px = np.full(len(df15), np.nan)
    imm_k = np.full(len(df15), -1, dtype=int)     # 트리거된 하위봉 위치
    done = 0
    for i in test_idx:
        t = df15.index[i]
        subs = groups.get(t)
        if subs is None or len(subs) == 0:
            continue
        base = df15.iloc[i - WIN + 1:i]           # i 이전 확정봉 (WIN-1개)
        o = float(subs.iloc[0]["open"]); hi = -1e18; lo = 1e18
        fired = 0; px = np.nan; kfire = -1
        for k, (_, s) in enumerate(subs.iterrows()):
            hi = max(hi, float(s["high"])); lo = min(lo, float(s["low"]))
            c = float(s["close"])
            forming = pd.DataFrame([{ "open": o, "high": hi, "low": lo,
                                      "close": c, "volume": 0.0}], index=[t])
            win = pd.concat([base, forming])
            sig = strategy.build_signals(win, df1h, df4h, df1d, CFG)
            r = sig.iloc[-1]
            if r["long"]:
                fired, px, kfire = 1, c, k; break
            if r["short"]:
                fired, px, kfire = -1, c, k; break
        imm_dir[i] = fired; imm_px[i] = px; imm_k[i] = kfire
        done += 1
        if done % 300 == 0:
            print(f"  ...{done}/{len(test_idx)} 봉 처리")
    return imm_dir, imm_px, imm_k


def _close_pos(pos, fill, reason, cfg, equity, trades):
    fill = fill * (1 - cfg["exit_slip"] * pos["dir"])
    gross = (fill - pos["entry"]) * pos["dir"] * pos["qty"]
    fee = (pos["entry"] * cfg["maker_fee"] + fill * cfg["taker_fee"]) * pos["qty"]
    equity += gross - fee
    trades.append({"dir": pos["dir"], "pnl": gross - fee, "reason": reason})
    return equity


def simulate(sig, entry_dir, entry_px, cfg, start, end, entry_k=None, subs_map=None,
             dir_filter=None):
    """공통 청산 엔진. entry_dir[i]/entry_px[i]로 진입.
    entry_k/subs_map 주어지면(즉시진입) 진입한 그 봉의 잔여 하위봉에서 손절 체크.
    dir_filter: None=양방향 / 1=롱전용 / -1=숏전용 / 'trend'=상위TF bias 정렬방향만."""
    equity = cfg["start_equity"]; trades = []; pos = None
    for i in range(start, end):
        row = sig.iloc[i]
        if pos:   # 청산: 손절(봉 고저) 우선 → 반대신호(종가)
            ex, reason = None, None
            hit = row["low"] <= pos["sl"] if pos["dir"] == 1 else row["high"] >= pos["sl"]
            sx = row["long_exit"] if pos["dir"] == 1 else row["short_exit"]
            if hit: ex, reason = pos["sl"], "SL"
            elif sx: ex, reason = row["close"], "EXIT"
            if ex is not None:
                equity = _close_pos(pos, ex, reason, cfg, equity, trades); pos = None
        d = entry_dir[i]
        if dir_filter in (1, -1) and d != dir_filter:
            d = 0
        elif dir_filter == "trend" and d != 0:
            b = row["bias"]
            if not ((d == 1 and b > 0) or (d == -1 and b < 0)):
                d = 0
        if pos is None and d != 0 and not np.isnan(row["atr"]) and not np.isnan(entry_px[i]):
            entry = entry_px[i]
            sl = row["swing_low"] if d == 1 else row["swing_high"]
            if pd.isna(sl) or (d == 1 and sl >= entry) or (d == -1 and sl <= entry):
                sl = entry - row["atr"] * cfg["atr_stop_mult"] * d
            dist = abs(entry - sl)
            qty = (equity * cfg["risk_per_trade"]) / dist if dist > 0 else 0
            if qty > 0:
                pos = {"dir": d, "entry": entry, "sl": sl, "qty": qty}
                # 즉시진입: 진입한 봉의 잔여 하위봉에서 손절 닿으면 그 봉 안에서 청산
                if subs_map is not None and entry_k is not None and entry_k[i] >= 0:
                    subs = subs_map.get(sig.index[i])
                    if subs is not None:
                        for s in subs.iloc[entry_k[i] + 1:].itertuples():
                            if (d == 1 and s.low <= pos["sl"]) or (d == -1 and s.high >= pos["sl"]):
                                equity = _close_pos(pos, pos["sl"], "SL_intrabar", cfg, equity, trades)
                                pos = None; break
    return pd.DataFrame(trades), equity


def build_lag_px(df15, subs_map, imm_dir, imm_k):
    """체결 현실화①(지연): 트리거 다음 15분 하위봉의 '시가'에 체결(약 5~15분 지연)."""
    px = np.full(len(df15), np.nan)
    for i in range(len(df15)):
        if imm_dir[i] == 0 or imm_k[i] < 0:
            continue
        subs = subs_map.get(df15.index[i])
        if subs is None:
            continue
        k = imm_k[i]
        if k + 1 < len(subs):
            px[i] = float(subs.iloc[k + 1]["open"])
        elif i + 1 < len(df15):                  # 봉 마지막 하위봉이면 다음 봉 시가
            nx = subs_map.get(df15.index[i + 1])
            if nx is not None and len(nx):
                px[i] = float(nx.iloc[0]["open"])
    return px


def build_limit(df15, subs_map, imm_dir, imm_px, imm_k, offset):
    """체결 현실화②(지정가/메이커): 신호가에서 유리한 쪽 offset 떨어진 지정가.
    같은 봉 잔여 하위봉이 그 가격을 건드려야 체결, 아니면 미체결(거래 스킵)."""
    px = np.full(len(df15), np.nan)
    d_out = imm_dir.copy()
    for i in range(len(df15)):
        d = imm_dir[i]
        if d == 0 or imm_k[i] < 0 or np.isnan(imm_px[i]):
            continue
        subs = subs_map.get(df15.index[i])
        if subs is None:
            d_out[i] = 0; continue
        lim = imm_px[i] * (1 - offset) if d == 1 else imm_px[i] * (1 + offset)
        filled = False
        for s in subs.iloc[imm_k[i]:].itertuples():   # 트리거봉 포함 잔여 구간
            if (d == 1 and s.low <= lim) or (d == -1 and s.high >= lim):
                filled = True; break
        if filled:
            px[i] = lim
        else:
            d_out[i] = 0          # 미체결 → 진입 안 함
    return d_out, px


def metrics(trades, eq, cfg):
    if trades.empty:
        return 0, 0.0, 0.0, 0.0
    n = len(trades); wins = trades[trades["pnl"] > 0]; los = trades[trades["pnl"] <= 0]
    pf = wins["pnl"].sum() / abs(los["pnl"].sum()) if len(los) and los["pnl"].sum() else float("inf")
    return n, len(wins) / n * 100, pf, (eq / cfg["start_equity"] - 1) * 100


def stats(name, trades, eq, cfg):
    if trades.empty:
        print(f"[{name}] 거래 없음"); return
    n = len(trades); wins = trades[trades["pnl"] > 0]; los = trades[trades["pnl"] <= 0]
    pf = wins["pnl"].sum() / abs(los["pnl"].sum()) if len(los) and los["pnl"].sum() else float("inf")
    ret = (eq / cfg["start_equity"] - 1) * 100
    print(f"[{name}]  거래 {n}  승률 {len(wins)/n*100:4.1f}%  "
          f"PF {pf:4.2f}  수익률 {ret:+6.1f}%  총손익 {trades['pnl'].sum():+,.0f}")


def main():
    # 상위TF 환산 비율(주력봉 1개당 상위봉 비율의 역수) — 대략치
    ratio = {"15m": {"1h": 4, "4h": 16, "1d": 96},
             "1h": {"4h": 4, "1d": 24, "1w": 168}}
    print(f"데이터 수집(주력 {PRIMARY} / 형성 {SUB} / 상위 {HTF})...")
    df15 = data.get_history("BTCUSDT", PRIMARY, bars=N_TEST + WIN + 50)
    r = ratio.get(PRIMARY, {})
    df1h = data.get_history("BTCUSDT", HTF[0], bars=int(N_TEST / r.get(HTF[0], 4)) + 500)
    df4h = data.get_history("BTCUSDT", HTF[1], bars=int(N_TEST / r.get(HTF[1], 16)) + 400)
    df1d = data.get_history("BTCUSDT", HTF[2], bars=int(N_TEST / r.get(HTF[2], 96)) + 300)
    df5 = data.get_history("BTCUSDT", SUB, bars=(N_TEST + 5) * SUBPER)
    print(f"{PRIMARY} {len(df15)}봉, {SUB} {len(df5)}봉, "
          f"상위 {HTF[0]} {len(df1h)} / {HTF[1]} {len(df4h)} / {HTF[2]} {len(df1d)}")

    sig = strategy.build_signals(df15, df1h, df4h, df1d, CFG)   # 확정신호(종가 기준)
    start = len(df15) - N_TEST
    end = len(df15)
    test_idx = list(range(start, end))

    # 확정 모드: 진입=마감종가, 방향=확정 entry 플래그
    conf_dir = np.where(sig["long"].values, 1, np.where(sig["short"].values, -1, 0)).astype(float)
    conf_px = sig["close"].values.copy()

    import os, pickle
    key = f"{PRIMARY}_{N_TEST}_{len(df15)}_{int(df15.index[-1].timestamp())}"
    cache = f"intrabar_cache_{key}.pkl"
    if os.path.exists(cache):
        print(f"캐시 사용: {cache} (재구성 생략)")
        with open(cache, "rb") as f:
            imm_dir, imm_px, imm_k = pickle.load(f)
    else:
        print("즉시신호 탐지(형성봉 재구성)... (수 분 소요)")
        imm_dir, imm_px, imm_k = detect_immediate(df15, df5, df1h, df4h, df1d, test_idx)
        with open(cache, "wb") as f:
            pickle.dump((imm_dir, imm_px, imm_k), f)
    subs_map = {ts: g for ts, g in df5.groupby(df5.index.floor(FLOOR))}

    # 휩쏘 통계: 즉시 켜졌으나 마감 미확정
    both_idx = np.array(test_idx)
    imm_on = imm_dir[both_idx] != 0
    conf_on = conf_dir[both_idx] != 0
    whip = int(np.sum(imm_on & ~conf_on))
    same = int(np.sum(imm_on & conf_on))
    print(f"\n즉시 트리거 {int(imm_on.sum())}회 중 — 마감 확정 {same} / 휩쏘(미확정) {whip}")

    # ── ① 체결 현실화: 진입가/체결가정만 바꿔 비교 (모두 봉내손절 반영)
    lag_px = build_lag_px(df15, subs_map, imm_dir, imm_k)
    lim_dir, lim_px = build_limit(df15, subs_map, imm_dir, imm_px, imm_k, CFG["limit_offset"])
    runs = {
        "확정진입(마감종가)":        (conf_dir, conf_px),
        "즉시-신호가체결(낙관)":      (imm_dir, imm_px),
        "즉시-다음봉시가(지연)":      (imm_dir, lag_px),
        "즉시-지정가/메이커(미체결반영)": (lim_dir, lim_px),
    }
    print("\n=== ① 체결 현실화 비교 (1년, 봉내손절 반영) ===")
    print(f"{'모드':<26}{'거래':>5}{'승률':>7}{'PF':>6}{'수익률':>8}")
    results = {}
    for name, (ed, ep) in runs.items():
        tr, eq = simulate(sig, ed, ep, CFG, start, end, entry_k=imm_k, subs_map=subs_map)
        results[name] = (ed, ep)
        n, wr, pf, ret = metrics(tr, eq, CFG)
        print(f"{name:<26}{n:>5}{wr:>6.1f}%{pf:>6.2f}{ret:>+7.1f}%")

    # ── ② 워크포워드: 1년을 4분기로 쪼개 구간별 견고성 확인
    print("\n=== ② 워크포워드 (분기별, 봉내손절 반영) ===")
    print(f"{'구간':<8}{'확정 PF/수익':>16}{'즉시(지연) PF/수익':>20}")
    seg = (end - start) // 4
    for q in range(4):
        s = start + q * seg
        e_ = end if q == 3 else start + (q + 1) * seg
        ctr, ceq = simulate(sig, conf_dir, conf_px, CFG, s, e_, entry_k=imm_k, subs_map=subs_map)
        itr, ieq = simulate(sig, imm_dir, lag_px, CFG, s, e_, entry_k=imm_k, subs_map=subs_map)
        _, _, cpf, cret = metrics(ctr, ceq, CFG)
        _, _, ipf, iret = metrics(itr, ieq, CFG)
        d0, d1 = df15.index[s], df15.index[e_ - 1]
        print(f"Q{q+1} {d0:%m/%d}~{d1:%m/%d}  {cpf:>5.2f} / {cret:>+6.1f}%   {ipf:>5.2f} / {iret:>+6.1f}%")

    # ── ③ 방향 필터: 양방향 vs 롱전용 vs 숏전용 vs 추세정렬 (즉시-지연 진입)
    def run_f(s, e_, df):
        tr, eq = simulate(sig, imm_dir, lag_px, CFG, s, e_,
                          entry_k=imm_k, subs_map=subs_map, dir_filter=df)
        return metrics(tr, eq, CFG)
    print("\n=== ③ 방향 필터 비교 (즉시-지연 진입, 봉내손절) ===")
    print(f"{'구간':<14}{'양방향':>14}{'롱전용':>14}{'숏전용':>14}{'추세정렬':>14}")
    rows = [("연간", start, end)] + [(f"Q{q+1}", start + q * seg,
            end if q == 3 else start + (q + 1) * seg) for q in range(4)]
    for name, s, e_ in rows:
        cells = []
        for df in (None, 1, -1, "trend"):
            n, wr, pf, ret = run_f(s, e_, df)
            cells.append(f"{ret:>+6.1f}%({n})")
        print(f"{name:<14}" + "".join(f"{c:>14}" for c in cells))


if __name__ == "__main__":
    # 사용법: python intrabar_test.py [primary] [n_test]
    #   기본:     15m 주력, 5m 형성, 상위 1h/4h/1d
    #   1h 모드:  python intrabar_test.py 1h 8760   → 1h 주력, 15m 형성, 상위 4h/1d/1w
    if len(sys.argv) > 1 and sys.argv[1] in ("1h", "15m"):
        PRIMARY = sys.argv[1]
        N_TEST = int(sys.argv[2]) if len(sys.argv) > 2 else (8760 if PRIMARY == "1h" else 2500)
        if PRIMARY == "1h":
            SUB, FLOOR, SUBPER, HTF = "15m", "1h", 4, ("4h", "1d", "1w")
        else:
            SUB, FLOOR, SUBPER, HTF = "5m", "15min", 3, ("1h", "4h", "1d")
    main()
