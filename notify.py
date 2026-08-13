"""텔레그램 발송 — 정식 모드 / 진단(테스트) 모드"""
import os, html, requests
import config

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT  = os.environ.get("TELEGRAM_CHAT_ID", "")

BADGE = {"S": "🚨", "A": "🔥", "B": "✅", "C": "📋", "D": "🗑"}


def send(text: str) -> bool:
    if not TOKEN or not CHAT:
        print("[텔레그램 미설정 — 콘솔 출력]\n" + text + "\n")
        return False
    r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    if r.status_code != 200:
        print("[텔레그램 실패]", r.status_code, r.text[:300])
        return False
    return True


def _esc(s, n=55):
    return html.escape(str(s)[:n])


# ── 정식 발송 (상품별 카드) ─────────────────────────────────
import re as _re

_COUNT = _re.compile(r"(\d+)\s*(개입|개|매|팩|입|정|포|캡슐|롤|장|구|병|캔)\b")
_VOL   = _re.compile(r"\d+(?:\.\d+)?\s*(?:kg|g|ml|mL|L|l)\b")


WEEKDAY = ["월", "화", "수", "목", "금", "토", "일"]


def _when(when):
    return f"{when:%m/%d}({WEEKDAY[when.weekday()]}) {when:%H:%M}"


def _greeting(when):
    h = when.hour
    if h < 10:  return "☀️ 아침 특가"
    if h < 15:  return "🍱 점심 특가"
    if h < 21:  return "🌆 저녁 특가"
    return "🌙 심야 특가"


def _cat(name):
    return config.CAT_EMOJI.get((name or "").strip(), "🛒")


def _spec(name):
    """상품명에서 규격 요약: '2L × 24개' 같은 한 줄"""
    v = _VOL.search(name)
    c, best = None, 1
    for num, unit in _COUNT.findall(name):
        n = int(num)
        if n > best:
            best, c = n, f"{n}{unit}"
    parts = [p for p in (v.group(0).replace(" ", "") if v else None, c) if p]
    return " × ".join(parts)


def _unit_price(name, price):
    """개당 단가 — 생필품에서 가장 실용적인 정보"""
    best = 1
    for num, _u in _COUNT.findall(name):
        best = max(best, int(num))
    return round(price / best) if best > 1 else None


def _rise(item):
    if not item.get("rising"):
        return ""
    return (f"🚀 <b>{config.LABEL_RISING}</b> — "
            f"판매순위 {item['rank_prev']}위 → <b>{item.get('rank')}위</b>\n")


def _meta_line(item, price):
    bits = [f"{_cat(item.get('category_name'))} {_esc(item.get('category_name') or '핫딜', 14)}"]
    sp = _spec(item["name"])
    if sp:
        bits.append(sp)
    bits.append("로켓배송" if item.get("is_rocket") else "일반배송")
    return " · ".join(bits)


def _tail(item, price):
    up = _unit_price(item["name"], price)
    extra = f"  (개당 {up:,}원)" if up else ""
    return extra


def deal_caption(h):
    """급락 상품 카드 — 가격비교 필수 + 왜 이렇게 싼지 설명"""
    if h.get("digit_error"):
        urgent = ("⚡ <b>가격 입력 오류 추정!</b>\n"
                  "<i>판매자가 0을 하나 빠뜨린 것으로 보입니다.</i>\n"
                  "<i>보통 몇 분~몇 시간 안에 정정되거나 품절됩니다. 서두르세요.</i>\n\n")
    elif h.get("grade") == "S":
        urgent = ("⚡ <b>비정상 급락!</b>\n"
                  "<i>평소 가격과 자릿수가 다릅니다. 곧 사라질 수 있습니다.</i>\n\n")
    else:
        urgent = ("🔥 <b>가격 급락 포착!</b>\n"
                  "<i>이 상품이 평소 팔리던 값보다 크게 떨어졌습니다.</i>\n\n")
    return (f"{urgent}{_rise(h)}🎁 <b>{h['off']}% 파격 특가!</b>\n"
            f"<b>{_esc(h['name'], 70)}</b>\n"
            f"{_meta_line(h, h['price'])}\n\n"
            f"　{h['ref_label']}　<s>{h['ref']:,}원</s>\n"
            f"🔥 <b>지금　　{h['price']:,}원</b>\n"
            f"💰 <b>{h['cut']:,}원 ↓  ({h['off']}% 할인)</b>{_tail(h, h['price'])}\n\n"
            f"🔗 <a href=\"{h['url']}\">쿠팡에서 보기</a>")


def gold_caption(g):
    """오늘의 핫딜 카드. 비교가 없는 상품은 애초에 안 들어옴."""
    return (f"{_rise(g)}⭐ <b>{config.LABEL_DAILY}  {g['off']}% 할인!</b>\n"
            f"<b>{_esc(g['name'], 70)}</b>\n"
            f"{_meta_line(g, g['price'])}\n\n"
            f"　{g['ref_label']}　<s>{g['ref']:,}원</s>\n"
            f"🔥 <b>지금　　{g['price']:,}원</b>\n"
            f"💰 <b>{g['cut']:,}원 ↓  ({g['off']}% 할인)</b>{_tail(g, g['price'])}\n\n"
            f"🔗 <a href=\"{g['url']}\">쿠팡에서 보기</a>")


def send_photo(photo_url, caption) -> bool:
    """썸네일 + 설명 카드. 이미지 실패 시 텍스트로 자동 대체."""
    if not TOKEN or not CHAT:
        print("[텔레그램 미설정 — 콘솔]\n" + caption + "\n")
        return False
    if photo_url:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                          json={"chat_id": CHAT, "photo": photo_url,
                                "caption": caption[:1024], "parse_mode": "HTML"},
                          timeout=25)
        if r.status_code == 200:
            return True
        print("[사진 실패 → 텍스트 대체]", r.status_code, r.text[:160])
    return send(caption)


def header_message(n_hits, n_golds, when):
    p = [f"🔥 <b>{config.CHANNEL_TITLE}</b>",
         f"{_greeting(when)}  {_when(when)}", ""]
    if n_hits:
        p.append(f"🔥 {config.LABEL_CRASH} <b>{n_hits}건</b>")
    if n_golds:
        p.append(f"⭐ {config.LABEL_DAILY} <b>{n_golds}건</b>")
    p.append(f"\n<i>모든 상품은 이전 판매가와 비교해 표시됩니다.</i>")
    p.append("\n<i>가격·재고는 실시간 변동됩니다. 구매 전 확인하세요.</i>")
    p.append("<i>이 채널은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.</i>")
    return "\n".join(p)


def _row(i, x):
    up = _unit_price(x["name"], x["price"])
    unit = f" · 개당 {up:,}원" if up else ""
    hot = "🚀 " if x.get("rising") else ""
    return (f"\n\n{i}. {hot}<b>{x['off']}%↓</b> {_cat(x.get('category_name'))} "
            f"<a href=\"{x['url']}\">{_esc(x['name'], 40)}</a>\n"
            f"　 <s>{x['ref']:,}원</s> → <b>{x['price']:,}원</b>  "
            f"💰{x['cut']:,}원↓{unit}")


def list_message(hits, golds, when, skip_id=None):
    """대표 카드 외 나머지를 한 메시지로 (도배 방지). 전부 가격비교 포함."""
    p, i = [], 0
    rest_h = [h for h in hits if h["product_id"] != skip_id]
    rest_g = [g for g in golds if g["product_id"] != skip_id]

    if rest_h:
        p.append(f"🔥 <b>{config.LABEL_CRASH}</b>")
        for h in rest_h:
            i += 1
            p.append(_row(i, h))
    if rest_g:
        p.append(("\n\n" if rest_h else "") + f"⭐ <b>{config.LABEL_DAILY}</b>")
        for g in rest_g:
            i += 1
            p.append(_row(i, g))
    if not p:
        return ""
    return "".join(p) + config.FOOTER


def hunt_header(n, when):
    return (f"🔥 <b>{config.LABEL_CRASH}!</b>  {_when(when)}\n"
            f"<i>평소 판매가보다 값이 크게 떨어진 상품 {n}건을 방금 발견했습니다.</i>")


def pinned_notice():
    """채널 상단 고정용 안내문. 발송 시각은 config에서 자동으로 읽어온다."""
    times = " · ".join(f"{h:02d}:{m:02d}" for h, m in config.DAILY_SLOTS)
    cnt = len(config.DAILY_SLOTS)
    return (
        f"📌 <b>{config.CHANNEL_TITLE}에 오신 것을 환영합니다</b>\n\n"

        "<b>━━ 어떤 알림이 오나요 ━━</b>\n\n"

        f"<b>① {config.LABEL_DAILY} — 하루 {cnt}번</b>\n"
        f"🕐 {times}\n"
        "쿠팡에서 오늘 가장 싸게 나온 상품들을 모아서 올려드립니다.\n\n"

        f"<b>② {config.LABEL_CRASH} — 시간 정해져 있지 않음</b>\n"
        "평소보다 값이 뚝 떨어진 상품이 없는지 <b>3분마다 계속 감시</b>합니다.\n"
        "발견하는 <b>즉시</b> 바로 올려드립니다. 알림을 켜두세요.\n\n"

        f"<b>③ 🚀 {config.LABEL_RISING} 상품</b>\n"
        "<b>지금 갑자기 잘 팔리기 시작한 상품</b>을 자동으로 찾아냅니다.\n"
        "판매순위가 <b>62위 → 9위</b>처럼 확 뛴 상품에 🚀 표시가 붙습니다.\n"
        "남들이 몰려가기 <b>전에</b> 먼저 확인하세요.\n\n"

        "<b>━━ ⚡ 표시가 붙은 상품은 뭔가요 ━━</b>\n\n"
        "판매자가 가격을 입력하다가 <b>0을 하나 빠뜨리는 실수</b>가 종종 있습니다.\n"
        "89만원짜리 건조기가 <b>8만 9천원</b>으로 올라가는 식입니다.\n\n"
        "이런 상품은 보통 <b>몇 분에서 몇 시간 안에</b> 정정되거나 품절됩니다.\n"
        "⚡ 표시가 보이면 고민하지 마시고 바로 확인하세요.\n\n"

        "<b>━━ 💰 가격은 항상 비교해서 보여드립니다 ━━</b>\n\n"
        "<code>　정상가　26,900원\n"
        "🔥 지금　　18,900원\n"
        "💰 8,000원 ↓ (30% 할인)</code>\n\n"
        "비교할 가격을 확인하지 못한 상품은 <b>아예 올리지 않습니다.</b>\n\n"

        "<b>━━ ⚠️ 꼭 알아두세요 ━━</b>\n\n"
        "· 가격 오류 상품은 <b>판매자가 주문을 취소할 수 있습니다</b>\n"
        "· 재고와 가격은 실시간으로 바뀝니다\n"
        "· 구매 전 쿠팡 페이지에서 <b>최종 가격을 반드시 확인</b>하세요\n"
        "· 수량 한정 상품이 많아 금방 사라집니다\n\n"

        "<i>이 채널은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.</i>"
    )


# ── 진단(테스트) 발송 ───────────────────────────────────────
def test_message(rows, stats, when):
    """등급/점수/괴리율/세부배점을 전부 보여줌. 임계값 조정용."""
    head = (f"🧪 <b>테스트 리포트</b>  {when:%m/%d %H:%M}\n"
            f"<code>수집 {stats['collected']} · 판정 {stats['matched']} · "
            f"이력축적중 {stats['warming']}</code>\n"
            f"<code>등급 S{stats['S']} A{stats['A']} B{stats['B']} "
            f"C{stats['C']} D{stats['D']}</code>\n"
            f"<code>발송컷 ratio≤{config.SEND_RATIO} / 하락≥{config.MIN_SAVING:,}</code>\n")
    lines = [head]
    for r in rows[:config.TEST_MAX_ITEMS]:
        parts = " ".join(f"{k}{v:+d}" for k, v in r["detail"].items() if v)
        lines.append(
            f"\n{BADGE.get(r['grade'],'·')} <b>{r['grade']}</b> "
            f"<code>{r['score']}점 r={r['ratio']:.2f}</code>\n"
            f"<a href=\"{r['url']}\">{_esc(r['name'], 45)}</a>\n"
            f"<code>현재 {r['cp']:,} vs 평소 {r['np']:,} (표본 {r['n_points']})</code>\n"
            f"<code>{parts}</code>")

    if stats["rejects"]:
        top = sorted(stats["rejects"].items(), key=lambda x: -x[1])[:6]
        lines.append("\n\n<b>탈락 사유 TOP</b>\n" +
                     "\n".join(f"<code>{_esc(k,30)}: {v}</code>" for k, v in top))
    return "".join(lines)


def selftest_message(results):
    ok = lambda b: "✅" if b else "❌"
    return ("🔧 <b>연결 점검</b>\n" +
            "\n".join(f"{ok(v)} {k}" for k, v in results.items()))
