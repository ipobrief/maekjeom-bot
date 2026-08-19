import re

# strategy.py
c = open('strategy.py', encoding='utf-8').read()
c = c.replace('    LR4 = macd_gc | (macd_line > 0)                    # MACD GC/0선위', '    LR4 = macd_gc                                       # MACD 골든크로스')
c = c.replace('    LR6 = rci_long > 0                                 # RCI 0선 위', '    rci_rising = (rci_long > rci_long.shift(1)) & (rci_long.shift(1) > rci_long.shift(2))\n    LR6 = (rci_long > 0) | rci_rising                 # RCI 0선 위 또는 2봉 연속 상승전환')
c = c.replace('    SR4 = macd_dc | (macd_line < 0)', '    SR4 = macd_dc                                       # MACD 데드크로스')
c = c.replace('    out["long"], out["short"] = long_entry, short_entry', '    out["long"], out["short"] = long_entry, short_entry\n    out["long_all"], out["short_all"] = long_all, short_all')
c = c.replace('    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)', '    direction = "LONG" if r["long"] else ("SHORT" if r["short"] else None)\n    direction_active = "LONG" if r.get("long_all", r["long"]) else ("SHORT" if r.get("short_all", r["short"]) else None)')
c = c.replace('        "direction": direction,', '        "direction": direction,\n        "direction_active": direction_active,')
open('strategy.py', 'w', encoding='utf-8').write(c)
print('strategy.py 완료')

# ws_watch.py
c = open('ws_watch.py', encoding='utf-8').read()
c = c.replace('    d = e["direction"]\n    # 같은 봉', '    d = e.get("direction_active", e["direction"])\n    # 같은 봉')
open('ws_watch.py', 'w', encoding='utf-8').write(c)
print('ws_watch.py 완료')

# ws_watch_1m.py
c = open('ws_watch_1m.py', encoding='utf-8').read()
c = c.replace('    d = e["direction"]\n    if d and d not in st.alerted_dirs:', '    d = e.get("direction_active", e["direction"])\n    if d and d not in st.alerted_dirs:')
open('ws_watch_1m.py', 'w', encoding='utf-8').write(c)
print('ws_watch_1m.py 완료')
