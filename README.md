# 쿠팡 딜 알림 봇 v2

네이버 쇼핑 최저가를 기준가로 삼아, 과거 히스토리 없이도 첫날부터 판정하는 구조.

## 확정된 설계

| 항목 | 결정 |
|---|---|
| 대상 | 생필품 7개 카테고리 베스트 100개 + 골드박스 |
| 기준가 | 네이버 쇼핑 **가격비교 카탈로그** 최저가 중앙값 (쿠팡 제외) |
| 스마트스토어 | 기준가 아님. **교차검증용** — 카탈로그와 3배 이상 벌어지면 폐기 |
| 배송비 | 양쪽 보정 (쿠팡 비로켓 +3,000 / 네이버 ×1.05) |
| 수집컷 | 괴리율 ≤ 0.90 (10% 할인) — 로그만 |
| 발송컷 | 괴리율 ≤ 0.70 **AND** 절약액 ≥ 15,000원 |
| 가격오류 | `쿠팡가 × 10`이 기준가의 80~120% → S등급 |
| 주기 | KST 07:10 / 12:00 / 18:00 / 22:00 |

## 4가지 실행 모드

| 모드 | 동작 |
|---|---|
| `selftest` | 쿠팡·네이버·텔레그램 연결만 확인 |
| `test` | 수집→판정→**진단 리포트** 발송 (점수·괴리율·배점·탈락사유 전부 표시) |
| `dry` | 콘솔 출력만 |
| `live` | 실제 딜 발송 |

**튜닝은 `test` 모드로 합니다.** 리포트에 왜 그 등급이 나왔는지 배점이 다 찍히므로,
텔레그램만 보면서 `config.py` 숫자를 고칠 수 있습니다.

---

## 세팅 (순서대로)

### 1. 텔레그램
1. `@BotFather` → `/newbot` → **토큰** 복사
2. 채널 생성 → 관리자에 봇 추가 (게시 권한 필수)
3. 채널에 아무 글 하나 올리고 → `https://api.telegram.org/bot<토큰>/getUpdates`
   → `"chat":{"id":-100XXXXXXXXX}` 의 숫자가 **CHAT_ID**

### 2. 네이버 개발자센터
developers.naver.com → 애플리케이션 등록 → **검색** API 선택
→ Client ID / Client Secret (무료, 일 25,000회)

### 3. 쿠팡 파트너스
partners.coupang.com → Open API → ACCESS KEY / SECRET KEY

### 4. GitHub Secrets
Settings → Secrets and variables → Actions → **Secrets** 탭

| 이름 |
|---|
| `COUPANG_ACCESS_KEY` |
| `COUPANG_SECRET_KEY` |
| `NAVER_CLIENT_ID` |
| `NAVER_CLIENT_SECRET` |
| `TELEGRAM_BOT_TOKEN` |
| `TELEGRAM_CHAT_ID` |

같은 화면 **Variables** 탭에 `RUN_MODE` = `test` 추가
(스케줄 실행이 이 값을 따릅니다. 나중에 `live`로 바꾸면 정식 발송)

### 5. 첫 실행
Actions → `쿠팡 딜 봇` → Run workflow → 모드 `selftest` → 텔레그램에 ✅ 3개 확인
→ 다음에 `test` 로 한 번 → 진단 리포트 확인

---

## 튜닝 순서 (2주)

| 시점 | 할 일 |
|---|---|
| Day 1 | `selftest` → `test` 1회. **매칭률 확인** |
| Day 1~3 | 매칭률 70% 미만이면 `naver.py`의 `extract_keyword` 부터 손봄 |
| Day 3~7 | `RUN_MODE=test`로 방치. 리포트 보며 오탐 관찰 |
| Day 7 | `python calibrate.py` → 카테고리별 괴리율 히스토그램 |
| Day 8 | 히스토그램 근거로 `config.SEND_RATIO` 확정 |
| Day 10~14 | 오탐 0 확인되면 `RUN_MODE=live` |

### 자주 만지게 될 값 (`config.py`)

| 값 | 기본 | 올리면 / 내리면 |
|---|---|---|
| `SEND_RATIO` | 0.70 | 내리면 알림 줄고 정확해짐 |
| `MIN_SAVING` | 15000 | 올리면 소액 딜 제외 |
| `GRADE_B` | 50 | 내리면 발송 늘어남 |
| `MAX_ALERTS` | 10 | 1회 발송 상한 |
| `PER_CATEGORY` | 100 | 줄이면 네이버 API 절약 |

---

## 알려진 한계 (미리 알고 시작할 것)

1. **매칭률이 전부다.** 쿠팡 상품명 → 네이버 검색어 변환이 실패하면 그 상품은 판정 자체가 안 됨.
   `test` 리포트의 `kw:` 줄을 반드시 눈으로 확인할 것.
2. **인기도(rank) 가중치가 비인기 딜을 깎는다.** rank 90위대 상품은 60% 할인이어도 C로 떨어짐.
   이게 싫으면 `scoring.py`의 C항목 배점을 낮추고 그만큼 괴리율로 옮길 것.
3. **네이버 API 일 25,000회.** 하루 4타임 × 250건 = 1,000회라 여유 있음.
   `PER_CATEGORY`를 올릴 때만 주의.
4. **파트너스 링크 수수료 문구는 필수.** `config.FOOTER`에 이미 들어있으니 지우지 말 것.
