#!/bin/bash
# 외부 헬스체크(=GitHub Actions health-monitor.yml)가 SSH forced command로 실행.
# 서버 ~/healthcheck.sh 에 배포되며 ~/.ssh/authorized_keys 의 command= 로 지정됨.
# 봇 TF 개편 시 이 목록도 반드시 동기화할 것. (BTC 3 + XAU 3 = 6봇, 30분봉은 2026-08-23 신호중단)
fail=""
for u in 3m 5m 10m; do
  s=$(systemctl is-active maekjeom-bot-$u 2>/dev/null)
  [ "$s" = active ] || fail="$fail BTC-$u=$s"
done
for u in 3m 5m 10m; do
  s=$(systemctl is-active maekjeom-bot-xau-$u 2>/dev/null)
  [ "$s" = active ] || fail="$fail XAU-$u=$s"
done
if [ -z "$fail" ]; then echo OK; else echo "DOWN$fail"; fi
