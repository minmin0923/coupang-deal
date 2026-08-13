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


def _meta_line(item, price):
    bits = [f"{_cat(item.get('category_name'))} {_esc(item.get('category_name') or '특가', 14)}"]
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
    """급락 상품 카드"""
    off = int(round((1 - h["ratio"]) * 100))
    urgent = "🚨 <b>가격오류 의심 — 빠른 품절/취소 가능</b>\n" if h["grade"] == "S" else ""
    return (f"{urgent}🎁 <b>{off}% 파격 특가!</b>\n"
            f"<b>{_esc(h['name'], 70)}</b>\n"
            f"{_meta_line(h, h['cp'])}\n\n"
            f"💵 <s>{h['np']:,}원</s> → <b>{h['cp']:,}원</b>\n"
            f"💰 <b>{h['saving']:,}원 절약</b>{_tail(h, h['cp'])}\n"
            f"🔗 <a href=\"{h['url']}\">쿠팡에서 보기</a>")


def gold_caption(g):
    """골드박스 카드"""
    price, orig, rate = g["price"], g.get("orig_price") or 0, g.get("discount_rate") or 0
    lines = []
    if orig > price:
        off = rate or int(round((1 - price / orig) * 100))
        lines.append(f"🎁 <b>{off}% 특가!</b>")
    elif rate > 0:
        lines.append(f"🎁 <b>{rate}% 특가!</b>")
    else:
        lines.append("⭐ <b>오늘의 골드박스</b>")

    lines.append(f"<b>{_esc(g['name'], 70)}</b>")
    lines.append(_meta_line(g, price) + "\n")

    if orig > price:
        lines.append(f"💵 <s>{orig:,}원</s> → <b>{price:,}원</b>")
        lines.append(f"💰 <b>{orig - price:,}원 절약</b>{_tail(g, price)}")
    else:
        lines.append(f"💵 <b>{price:,}원</b>{_tail(g, price)}")
    lines.append(f"🔗 <a href=\"{g['url']}\">쿠팡에서 보기</a>")
    return "\n".join(lines)


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
        p.append(f"🚨 급락 포착 <b>{n_hits}건</b>")
    else:
        p.append("<i>이번 시간대 급락 포착 없음</i>")
    if n_golds:
        p.append(f"⭐ 오늘의 골드박스 <b>{n_golds}건</b>")
    p.append("\n<i>가격·재고는 실시간 변동됩니다. 구매 전 확인하세요.</i>")
    p.append("<i>이 채널은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.</i>")
    return "\n".join(p)


def pinned_notice():
    return (f"📌 <b>{config.CHANNEL_TITLE} 안내</b>\n\n"
            "쿠팡에서 <b>평소보다 크게 싸진 상품</b>과 "
            "<b>당일 골드박스 특가</b>를 하루 4번 자동으로 올려드립니다.\n\n"
            "🕖 07:10 · 🕛 12:00 · 🕕 18:00 · 🕙 22:00\n\n"
            "<b>표시 기준</b>\n"
            "· 급락 항목의 비교가격 = 최근 30일 평균 판매가\n"
            "· 골드박스 항목 = 쿠팡이 표시한 가격 기준\n"
            "· 🚨 표시 = 가격 입력 오류 의심\n\n"
            "<b>주의</b>\n"
            "· 특가는 수량 한정이라 금방 사라집니다\n"
            "· 가격오류 상품은 판매자가 주문을 취소할 수 있습니다\n"
            "· 구매 전 상품 페이지에서 최종 가격을 확인하세요\n\n"
            "<i>이 채널은 쿠팡 파트너스 활동의 일환으로 수수료를 제공받습니다.</i>")


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
