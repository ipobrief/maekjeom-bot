c = open('strategy.py', encoding='utf-8').read()

old = """    # ── 롱: 필수(선행스팬1·20일선) + 나머지6 중 rem_req개
    LM1 = d["close"] > senkou1                        # [필수] 선행스팬1 위
    LM2 = d["close"] > ma20                            # [필수] 20일선 위
    LR1 = chikou_above                                # 후행스팬 > 26봉전
    LR2 = tenkan > kijun                              # 전환선 > 기준선
    LR3 = d["close"] > res_line                        # 하락 대각선 상향돌파
    LR4 = macd_gc                                       # MACD 골든크로스
    LR5 = k > 50                                       # 스토 50 위
    rci_rising = (rci_long > rci_long.shift(1)) & (rci_long.shift(1) > rci_long.shift(2))
    LR6 = (rci_long > 0) | rci_rising                 # RCI 0선 위 또는 2봉 연속 상승전환
    long_rem = (LR1.astype(int) + LR2.astype(int) + LR3.astype(int)
                + LR4.astype(int) + LR5.astype(int) + LR6.astype(int))
    long_all = (LM1 & LM2 & (long_rem >= rem_req)).fillna(False)
    long_entry = long_all & ~long_all.shift(1, fill_value=False)

    # ── 숏: 거울
    SM1 = d["close"] < senkou1
    SM2 = d["close"] < ma20
    SR1 = chikou_below
    SR2 = tenkan < kijun
    SR3 = d["close"] < sup_line                        # 상승 대각선 하향이탈
    SR4 = macd_dc                                       # MACD 데드크로스
    SR5 = k < 50
    SR6 = rci_long < 0                                 # RCI 0선 아래
    short_rem = (SR1.astype(int) + SR2.astype(int) + SR3.astype(int)
                 + SR4.astype(int) + SR5.astype(int) + SR6.astype(int))
    short_all = (SM1 & SM2 & (short_rem >= rem_req)).fillna(False)
    short_entry = short_all & ~short_all.shift(1, fill_value=False)

    # ── 청산(익절) = 반대 셋업 형성 (매수익절=매도진입)
    long_exit = short_all
    short_exit = long_all"""

new = """    rci_rising  = (rci_long > rci_long.shift(1)) & (rci_long.shift(1) > rci_long.shift(2))
    rci_falling = (rci_long < rci_long.shift(1)) & (rci_long.shift(1) < rci_long.shift(2))

    # ── 공통 조건
    LM1 = d["close"] > senkou1                        # [필수] 선행스팬1 위
    LM2 = d["close"] > ma20                            # [필수] 20일선 위
    LR1 = chikou_above
    LR2 = tenkan > kijun
    LR3 = d["close"] > res_line
    LR5 = k > 50
    SM1 = d["close"] < senkou1
    SM2 = d["close"] < ma20
    SR1 = chikou_below
    SR2 = tenkan < kijun
    SR3 = d["close"] < sup_line
    SR5 = k < 50

    # ── 잠정(provisional): MACD GC/DC만, RCI 방향전환 포함
    LR4_p = macd_gc
    LR6_p = (rci_long > 0) | rci_rising
    SR4_p = macd_dc
    SR6_p = (rci_long < 0) | rci_falling
    long_rem_p  = (LR1.astype(int) + LR2.astype(int) + LR3.astype(int)
                   + LR4_p.astype(int) + LR5.astype(int) + LR6_p.astype(int))
    long_all_p  = (LM1 & LM2 & (long_rem_p  >= rem_req)).fillna(False)
    short_rem_p = (SR1.astype(int) + SR2.astype(int) + SR3.astype(int)
                   + SR4_p.astype(int) + SR5.astype(int) + SR6_p.astype(int))
    short_all_p = (SM1 & SM2 & (short_rem_p >= rem_req)).fillna(False)

    # ── 확정(confirmed): MACD GC/DC 또는 0선 위/아래, RCI 0선만
    LR4 = macd_gc | (macd_line > 0)
    LR6 = rci_long > 0
    SR4 = macd_dc | (macd_line < 0)
    SR6 = rci_long < 0
    long_rem  = (LR1.astype(int) + LR2.astype(int) + LR3.astype(int)
                 + LR4.astype(int) + LR5.astype(int) + LR6.astype(int))
    long_all  = (LM1 & LM2 & (long_rem  >= rem_req)).fillna(False)
    long_entry = long_all & ~long_all.shift(1, fill_value=False)
    short_rem = (SR1.astype(int) + SR2.astype(int) + SR3.astype(int)
                 + SR4.astype(int) + SR5.astype(int) + SR6.astype(int))
    short_all = (SM1 & SM2 & (short_rem >= rem_req)).fillna(False)
    short_entry = short_all & ~short_all.shift(1, fill_value=False)

    # ── 청산(익절) = 확정 반대 셋업
    long_exit = short_all
    short_exit = long_all"""

c = c.replace(old, new)
c = c.replace('    out["long_all"], out["short_all"] = long_all, short_all',
              '    out["long_all"], out["short_all"] = long_all_p, short_all_p  # 잠정용')
open('strategy.py', 'w', encoding='utf-8').write(c)
print('완료' if 'long_all_p' in open('strategy.py', encoding='utf-8').read() else '실패')
