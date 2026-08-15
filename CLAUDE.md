# 맥점 봇 운영 정보

## 서버
- Oracle Cloud (ap-tokyo-1), Ubuntu 22.04
- IP: 161.33.150.142
- 접속: ssh -i C:\Users\USER\.ssh\id_ed25519 ubuntu@161.33.150.142

## 봇 구성 (2026-08-04 시간대: 3m·5m·30m·1h. 15m·4h·1d 중단)
⚠️ **2026-08-15: BTC·XAU 8봇 전부 선물 REST 폴링(fapi)로 통일** (`FEED_MODE=poll`). 기존 BTC는 현물 WS 틱+선물 과거봉 **하이브리드**였는데, 현물이 선물보다 ~24p 높아 **현물 형성가를 선물 지표에 비교 → 허위 필수 성립**(09:55·17:33 허위신호). 특히 재시작 직후 df0가 순수 선물이라 증폭. 폴링 전환으로 과거봉·형성봉 모두 선물 → 네가 보는 BTCUSDT.P 차트와 일치, 혼합 허위 제거. 비용 봉마감 감지 ~15초. (XAU는 fstream 차단으로 원래 폴링.)
맥점신호 그룹(-1003964313330) 토픽으로 발송. 각 봉은 맥점방(후행·전환 정렬)/막돌파방 풀 라우팅.
- ws_watch.py — 1시간봉 (TELEGRAM_TOKEN, chris1H_bot, 맥점토픽 thread=5)
- ws_watch_5m.py — 5분봉 (chris15m_bot=TELEGRAM_TOKEN_1M → BotFather에서 chris5m_bot로 개명. 2026-08-04 15분봉 대체. 맥점토픽 THREAD_ID_5M=574)
- ws_watch_30m.py — 30분봉 (chris4h_bot=TELEGRAM_TOKEN_4H → chris30m_bot 개명, 맥점토픽 THREAD_ID_30M=559)
- ws_watch_3m.py — 3분봉 (chris1d_bot=TELEGRAM_TOKEN_1D → chris3m_bot 개명, 맥점토픽 THREAD_ID_3M=558)
- ~~ws_watch_1m.py(15m) / ws_watch_4h.py / ws_watch_1d.py~~ — 중단(서비스 disable, 토픽 삭제)
- ※ 다운감시(GH Actions)는 기존 개인 DM으로 유지(긴급알림 분리)

### 막돌파신호 방 (2026-07-25~) — 🎯 막돌파 전용 별도 그룹
- 그룹 "막돌파신호" chat_id=-1004425656249.
- 토픽(2026-08-04): 3분봉=14 / 5분봉=132 / 30분봉=85 / 1시간봉=4  (15분봉=2·4시간봉·1일봉 삭제)
- env: `TELEGRAM_CHAT_ID_BO=-1004425656249` + `TELEGRAM_BO_THREAD_3M=14/_5M=132/_30M=85/_1H=4`
- 맥점방 토픽: 3분봉=558 / 5분봉=574 / 30분봉=559 / 1시간봉=5.
- 동작: 막돌파(fresh≥3) + 후행·전환 정렬 → 맥점방 / 후행·전환 미완 → 막돌파방. 비막돌파는 발송 안 함.

### 다이버전스 발송 (2026-07-19 추가, 맥점과 별개 메시지)
각 봉이 다이버전스 카드를 **기존 토픽에 별도 메시지**로 발송(카드 헤더 🔀로 맥점과 구분).
- 기본: 기존 토픽으로 발송(`TELEGRAM_THREAD_ID`/`_1M`/`_4H`/`_1D` 폴백). **env 추가 불필요.**
- 전용 토픽 분리를 원하면 `TELEGRAM_DIV_THREAD_1M/_1H/_4H/_1D` 설정 시 그쪽 우선.
- 봇 토큰/챗ID는 기존 것 재사용.
- 경로: ~/maekjeom-bot
- 파이썬은 `python3` (이 서버엔 `python` 명령 없음 — nohup python 하면 Exit 127)

## 🥇 XAU(금 무기한선물) 봇 — 2026-08-12 추가
BTC와 **완전 동일한 막돌파/맥점 규칙**을 XAUUSDT 선물에 적용. 코드는 BTC와 같은 파일 공유,
`SYMBOL`·`WS_BASE` env override로 종목만 교체(별도 프로세스 → dedup 상태 격리, BTC 무영향).
- **데이터**: `fapi.binance.com` XAUUSDT klines(선물, REST). ⚠️**선물 웹소켓(fstream) 이 서버 IP에서 차단**
  — 연결은 되나 kline 데이터 미전송(선물 BTC조차 무수신 → 유동성 아닌 IP제한. 현물 WS·선물 REST는 정상).
  그래서 XAU는 **웹소켓 대신 REST 폴링**(`poll_feed.py`, env `FEED_MODE=poll`, 15s 간격)으로 fapi klines를 받아
  `handle_tick`에 공급(라우팅·카드·중복억제 로직 동일 재사용). BTC는 현물 WS 정상이라 그대로.
  ※ `WS_BASE` env는 폴링 모드에선 무시됨(유닛에 남아있어도 무해). XAU는 과거봉+형성봉 모두 선물(fapi)이라 순수 선물 신호.
- **발송처**: 기존 그룹 2개 재사용, XAU 전용 토픽 신설(봇 토큰·chat_id 재사용).
  - 맥점신호 그룹(-1003964313330) XAU 토픽: 3분봉=620 / 5분봉=621 / 30분봉=622 / 1시간봉=623
  - 막돌파신호 그룹(-1004425656249) XAU 토픽: 3분봉=205 / 5분봉=206 / 30분봉=207 / 1시간봉=208
- **systemd 4개**: `maekjeom-bot-xau-3m/-xau-5m/-xau-30m/-xau-1h` (각 유닛 `Environment=SYMBOL=XAUUSDT`,`FEED_MODE=poll`).
- **막돌파 창 `FRESH_BARS`(2026-08-14)**: XAU **5m·30m·1h 유닛에 `Environment=FRESH_BARS=3`**(15분 창 — 연속3봉 급락 포착). XAU 3m·BTC 전부는 미설정=기본 2봉. 코드는 각 CFG `fresh_bars=int(os.environ.get("FRESH_BARS","2"))`. FRESH_BARS는 어느 env파일에도 없어 inline `Environment=`로 안전(EnvironmentFile 충돌 없음). BTC 봇은 재시작 불필요(env 없어 동작 동일).
- ⚠️ **토픽 override는 반드시 EnvironmentFile로 (2026-08-13 버그수정)**: 공유 `/etc/maekjeom-bot.env`가 BTC 토픽
  (THREAD_ID_30M=559 등)을 정의하는데, **systemd는 `EnvironmentFile`이 inline `Environment=`를 항상 덮어씀** →
  유닛에 `Environment=TELEGRAM_THREAD_ID_30M=622`로 써도 무효(런타임 559로 새서 XAU 신호가 BTC 토픽으로 감).
  **해결**: XAU 토픽을 `/etc/maekjeom-bot-xau.env`에 모아두고, 각 XAU 유닛 드롭인
  `/etc/systemd/system/<unit>.service.d/override.conf`에 `[Service]\nEnvironmentFile=/etc/maekjeom-bot-xau.env` 추가
  (드롭인 파일이 공유파일보다 **나중에 로드→우선**). 유닛 inline thread override는 죽은 설정(무해하나 신뢰 금지).
  검증: `sudo tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value maekjeom-bot-xau-30m)/environ | grep THREAD_ID_30M` → 622.
  (`systemctl show -p Environment`는 inline만 보여줘 오해 유발 — 반드시 /proc/PID/environ로 실 런타임 확인.)
- **재시작**: `sudo systemctl restart maekjeom-bot-xau-3m maekjeom-bot-xau-5m maekjeom-bot-xau-30m maekjeom-bot-xau-1h`
- **상태**: `systemctl is-active maekjeom-bot-xau-{3m,5m,30m,1h}` / `ps aux|grep ws_watch` → BTC4+XAU4 = **8개**
- ⚠️ **금 휴장 주의**: 금 무기한은 주말·연말 등 underlying 휴장 때 거래정지/데이터 갭 가능(BTC 24/7과 다름).
  주말 첫 운영 시 봉 연속성·오작동 관찰 필요.

## ⚠️ 봇은 systemd로 관리됨 (nohup 쓰지 말 것)
두 봇은 systemd 서비스로 등록되어 있고 자동재시작(Restart=always)된다.
- maekjeom-bot.service       — 1시간봉 (1h)
- maekjeom-bot-5m.service    — 5분봉 (5m, 2026-08-04 15분봉 대체)
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
sudo systemctl restart maekjeom-bot maekjeom-bot-5m maekjeom-bot-30m maekjeom-bot-3m

## 봇 상태 확인 (서버에서)
systemctl status maekjeom-bot maekjeom-bot-5m maekjeom-bot-30m maekjeom-bot-3m --no-pager
ps aux | grep ws_watch          # BTC 4개 + XAU 4개 = 정상이면 8개 (2026-08-12 XAU 추가 전엔 4개)
sudo journalctl -u maekjeom-bot -f
sudo journalctl -u maekjeom-bot-5m -f

## 중복 프로세스 정리 (수동 nohup 등으로 중복 떴을 때)
pkill -9 -f ws_watch
sudo systemctl restart maekjeom-bot maekjeom-bot-5m maekjeom-bot-30m maekjeom-bot-3m

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
