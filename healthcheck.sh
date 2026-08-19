#!/bin/bash
# 외부 헬스체크(=GitHub Actions health-monitor.yml)가 SSH forced command로 실행.
# 서버 ~/healthcheck.sh 에 배포되며 ~/.ssh/authorized_keys 의 command= 로 지정됨.
# 봇 TF 개편 시 이 목록도 반드시 동기화할 것.
a=$(systemctl is-active maekjeom-bot-3m 2>/dev/null)
b=$(systemctl is-active maekjeom-bot-5m 2>/dev/null)
c=$(systemctl is-active maekjeom-bot-10m 2>/dev/null)
d=$(systemctl is-active maekjeom-bot-30m 2>/dev/null)
if [ "$a" = active ] && [ "$b" = active ] && [ "$c" = active ] && [ "$d" = active ]; then echo OK; else echo "DOWN 3m=$a 5m=$b 10m=$c 30m=$d"; fi
