# 맥점 봇 운영 정보

## 서버
- Oracle Cloud (ap-tokyo-1), Ubuntu 22.04
- IP: 161.33.150.142
- 접속: ssh -i C:\Users\USER\.ssh\id_ed25519 ubuntu@161.33.150.142

## 봇 구성 (2026-07-06부터 텔레그램 그룹 "맥점신호"(-1003964313330)의 토픽으로 발송)
- ws_watch.py — 1시간봉 봇 (TELEGRAM_TOKEN, 토픽 thread=5)
- ws_watch_1m.py — 15분봉 봇 (TELEGRAM_TOKEN_1M, 토픽 thread=2)
- ws_watch_1d.py — 일봉 봇 (TELEGRAM_TOKEN_1D, 토픽 thread=7)
- ※ 다운감시(GH Actions)는 기존 개인 DM으로 유지(긴급알림 분리)
- 경로: ~/maekjeom-bot
- 파이썬은 `python3` (이 서버엔 `python` 명령 없음 — nohup python 하면 Exit 127)

## ⚠️ 봇은 systemd로 관리됨 (nohup 쓰지 말 것)
두 봇은 systemd 서비스로 등록되어 있고 자동재시작(Restart=always)된다.
- maekjeom-bot.service       — 1시간봉 (1h)
- maekjeom-bot-15m.service   — 15분봉 (15m)
- maekjeom-bot-1d.service    — 일봉 (1d)

수동 `nohup python3 ...` 로 띄우면 systemd 봇과 중복 실행되어 텔레그램 알림이
겹친다. 반드시 systemctl 로 관리할 것.
(~/maekjeom-bot/start.sh 도 nohup 방식이라 사용 금지)

※ 재시작을 짧은 간격으로 두 번 하면 잠정신호가 두 번 나갈 수 있다(재시작 시
   형성봉을 처음부터 재평가 + 중복방지 기록 초기화). 평소엔 중복 안 남.

## 업데이트 & 재시작 절차 (서버에서)
cd ~/maekjeom-bot
git pull origin main
sudo systemctl restart maekjeom-bot maekjeom-bot-15m maekjeom-bot-1d

## 봇 상태 확인 (서버에서)
systemctl status maekjeom-bot maekjeom-bot-15m maekjeom-bot-1d --no-pager
ps aux | grep ws_watch          # 정상이면 ws_watch.py / ws_watch_1m.py / ws_watch_1d.py 딱 3개
sudo journalctl -u maekjeom-bot -f
sudo journalctl -u maekjeom-bot-15m -f

## 중복 프로세스 정리 (수동 nohup 등으로 중복 떴을 때)
pkill -9 -f ws_watch
sudo systemctl restart maekjeom-bot maekjeom-bot-15m maekjeom-bot-1d

## 코드 변경 → 배포 흐름
1. 코드 수정 후 GitHub main 에 push
   - ⚠️ 이 클라우드(Claude) 환경은 정책상 GitHub push가 차단됨(403).
     클라우드에서 수정한 경우 patch 파일을 받아 Windows에서 적용·push 해야 함.
   - Windows PowerShell (프롬프트가 `PS C:\...`):
       cd C:\Users\USER\maekjeom-bot
       git apply <패치파일>        # 다운로드 시 이름에서 하이픈 빠질 수 있으니 확인
       git add <변경파일>
       git commit -m "..."
       git push origin main
   - ⚠️ Windows 명령을 서버 SSH 창(`ubuntu@maekjeom-bot:~$`)에 붙여넣지 말 것.
2. 서버 SSH 접속 후 위 "업데이트 & 재시작 절차" 실행

## 주요 수정 이력
- 잠정신호 버그 수정(2026-06): 잠정 LONG 신호가 SHORT(전부 ❌)로 잘못 표시되던
  문제 해결(fmt_signal 에 active_dir 전달), 필수조건(선행스팬1·20일선) 미충족 시
  잠정신호 발송 억제. (strategy.py 의 direction_active 기반)
