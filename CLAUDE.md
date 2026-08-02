# 맥점 봇 운영 정보

## 서버
- Oracle Cloud (ap-tokyo-1), Ubuntu 22.04
- IP: 161.33.150.142
- 접속: ssh -i C:\Users\USER\.ssh\id_ed25519 ubuntu@161.33.150.142

## 봇 구성 (2026-08-02 시간대 재편: 3m·15m·30m·1h. 4h·1d 중단)
맥점신호 그룹(-1003964313330) 토픽으로 발송. 각 봉은 맥점방(후행·전환 정렬)/막돌파방 풀 라우팅.
- ws_watch.py — 1시간봉 (TELEGRAM_TOKEN, 맥점토픽 thread=5)
- ws_watch_1m.py — 15분봉 (TELEGRAM_TOKEN_1M, 맥점토픽 thread=2)
- ws_watch_30m.py — 30분봉 (chris4h_bot=TELEGRAM_TOKEN_4H 재사용, 맥점토픽 TELEGRAM_THREAD_ID_30M=559)
- ws_watch_3m.py — 3분봉 (chris15m_bot=TELEGRAM_TOKEN_1M 재사용, 맥점토픽 TELEGRAM_THREAD_ID_3M=558)
- ~~ws_watch_4h.py / ws_watch_1d.py~~ — 2026-08-02 중단(서비스 disable, 토픽 삭제)
- ※ 다운감시(GH Actions)는 기존 개인 DM으로 유지(긴급알림 분리)

### 막돌파신호 방 (2026-07-25~) — 🎯 막돌파 전용 별도 그룹
- 그룹 "막돌파신호" chat_id=-1004425656249.
- 토픽(2026-08-02): 3분봉=14 / 15분봉=2 / 30분봉=85 / 1시간봉=4  (4시간봉=6·1일봉=8 삭제)
- env: `TELEGRAM_CHAT_ID_BO=-1004425656249` + `TELEGRAM_BO_THREAD_3M=14/_1M=2/_30M=85/_1H=4`
- 맥점방 토픽: 3분봉=558 / 15분봉=2 / 30분봉=559 / 1시간봉=5.
- 동작: 막돌파(fresh≥3) + 후행·전환 정렬 → 맥점방 / 후행·전환 미완 → 막돌파방. 비막돌파는 발송 안 함.

### 다이버전스 발송 (2026-07-19 추가, 맥점과 별개 메시지)
각 봉이 다이버전스 카드를 **기존 토픽에 별도 메시지**로 발송(카드 헤더 🔀로 맥점과 구분).
- 기본: 기존 토픽으로 발송(`TELEGRAM_THREAD_ID`/`_1M`/`_4H`/`_1D` 폴백). **env 추가 불필요.**
- 전용 토픽 분리를 원하면 `TELEGRAM_DIV_THREAD_1M/_1H/_4H/_1D` 설정 시 그쪽 우선.
- 봇 토큰/챗ID는 기존 것 재사용.
- 경로: ~/maekjeom-bot
- 파이썬은 `python3` (이 서버엔 `python` 명령 없음 — nohup python 하면 Exit 127)

## ⚠️ 봇은 systemd로 관리됨 (nohup 쓰지 말 것)
두 봇은 systemd 서비스로 등록되어 있고 자동재시작(Restart=always)된다.
- maekjeom-bot.service       — 1시간봉 (1h)
- maekjeom-bot-15m.service   — 15분봉 (15m)
- maekjeom-bot-30m.service   — 30분봉 (30m, 2026-08-02 신설)
- maekjeom-bot-3m.service    — 3분봉 (3m, 풀 라우팅)
- ~~maekjeom-bot-4h / -1d~~  — 2026-08-02 중단(disable)

수동 `nohup python3 ...` 로 띄우면 systemd 봇과 중복 실행되어 텔레그램 알림이
겹친다. 반드시 systemctl 로 관리할 것.
(~/maekjeom-bot/start.sh 도 nohup 방식이라 사용 금지)

※ 재시작을 짧은 간격으로 두 번 하면 잠정신호가 두 번 나갈 수 있다(재시작 시
   형성봉을 처음부터 재평가 + 중복방지 기록 초기화). 평소엔 중복 안 남.

## 업데이트 & 재시작 절차 (서버에서)
cd ~/maekjeom-bot
git pull origin main
sudo systemctl restart maekjeom-bot maekjeom-bot-15m maekjeom-bot-30m maekjeom-bot-3m

## 봇 상태 확인 (서버에서)
systemctl status maekjeom-bot maekjeom-bot-15m maekjeom-bot-30m maekjeom-bot-3m --no-pager
ps aux | grep ws_watch          # 정상이면 ws_watch(.py/_1m/_30m/_3m) 딱 4개
sudo journalctl -u maekjeom-bot -f
sudo journalctl -u maekjeom-bot-15m -f

## 중복 프로세스 정리 (수동 nohup 등으로 중복 떴을 때)
pkill -9 -f ws_watch
sudo systemctl restart maekjeom-bot maekjeom-bot-15m maekjeom-bot-30m maekjeom-bot-3m

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
