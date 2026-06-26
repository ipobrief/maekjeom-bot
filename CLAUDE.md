# 맥점 봇 운영 정보

## 서버
- Oracle Cloud (ap-tokyo-1), Ubuntu 22.04
- IP: 161.33.150.142
- 접속: ssh -i C:\Users\USER\.ssh\id_ed25519 ubuntu@161.33.150.142

## 봇 구성
- ws_watch.py — 1시간봉 봇 (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID)
- ws_watch_1m.py — 15분봉 봇 (TELEGRAM_TOKEN_1M / TELEGRAM_CHAT_ID_1M)
- 경로: ~/maekjeom-bot
- 백그라운드 실행: nohup, 로그는 ws_watch.log / ws_watch_1m.log

## 업데이트 & 재시작 절차
cd ~/maekjeom-bot
git pull origin main
pkill -f ws_watch.py
nohup python ws_watch.py > ws_watch.log 2>&1 &
nohup python ws_watch_1m.py > ws_watch_1m.log 2>&1 &

## 봇 상태 확인
ps aux | grep ws_watch
tail -f ws_watch.log
tail -f ws_watch_1m.log

## 코드 변경 시
- Windows에서 수정 후 git push origin main
- 서버에서 위 업데이트 절차 실행
