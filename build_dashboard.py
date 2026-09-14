# -*- coding: utf-8 -*-
"""stats JSON → 대시보드 HTML. UTF-8 고정(윈도우 cp949 회피).
사용:
  python build_dashboard.py <stats.json> <out.html>
  또는 stdin/stdout: python build_dashboard.py < stats.json > out.html (POSIX)
템플릿(dashboard_template.html)의 __DATA_JSON__ 자리에 DATA를 주입."""
import sys
import os
import io
import json

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    if len(args) >= 1:
        raw = open(args[0], encoding="utf-8").read().strip()
    else:
        raw = sys.stdin.read().strip()
    data = json.loads(raw)
    # 시작자본 = 현재잔고 - 순손익(수수료포함). 없으면 잔고로 대체.
    data.setdefault("start_equity", round(data.get("balance", 0) - data.get("net_pnl", 0), 2))
    tmpl = open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8").read()
    html = tmpl.replace("__DATA_JSON__", json.dumps(data, ensure_ascii=False))
    if len(args) >= 2:
        with open(args[1], "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print("wrote", args[1], len(html), "chars")
    else:
        out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n")
        out.write(html)
        out.flush()


if __name__ == "__main__":
    main()
