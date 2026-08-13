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


# ── 정식 발송 ──────────────────────────────────────────────
def _greeting(when):
    h = when.hour
    if h < 10:  return "☀️ 아침 특가"
    if h < 15:  return "🍱 점심 특가"
    if h < 21:  return "🌆 저녁 특가"
    return "🌙 심야 특가"


def _cat(name):
    return config.CAT_EMOJI.get((name or "").strip(), "🛒")


def _deal_block(h):
    """급락 상품 한 덩어리"""
    off = int(round((1 - h["ratio"]) * 100))
    ship = "로켓배송" if h["is_rocket"] else "일반배송"
    urgent = "🚨 <b>가격오류 의심!</b>\n" if h["grade"] == "S" else ""
    return (f"\n{urgent}🎁 <b>{off}% 파격 특가!</b>\n"
            f"✅ {_cat(h['category_name'])} {_esc(h['name'], 60)}\n"
            f"↳ <s>{h['np']:,}원</s> → <b>{h['cp']:,}원</b> ({ship})\n"
            f"💰 {h['saving']:,}원 절약\n"
            f"🔗 <a href=\"{h['url']}\">최저가 보러가기</a>\n")


def _gold_block(g):
    ship = "로켓배송" if g["is_rocket"] else "일반배송"
    orig = g.get("orig_price") or 0
    rate = g.get("discount_rate") or 0
    if orig <= g["price"] and rate > 0:
        # 원가 없이 할인율만 있으면 역산하지 않는다(쿠팡 표시가와 어긋날 수 있음)
        return (f"\n🎁 <b>{rate}% 특가!</b>\n"
                f"✅ {_cat(g.get('category_name'))} {_esc(g['name'], 60)}\n"
                f"↳ <b>{g['price']:,}원</b> ({ship})\n"
                f"🔗 <a href=\"{g['url']}\">상품 보러가기</a>\n")
    if orig > g["price"]:
        off = rate or int(round((1 - g["price"] / orig) * 100))
        head = f"🎁 <b>{off}% 특가!</b>\n"
        price = f"↳ <s>{orig:,}원</s> → <b>{g['price']:,}원</b> ({ship})\n"
        save = f"💰 {orig - g['price']:,}원 절약\n"
    else:
        head, save = "", ""
        price = f"↳ <b>{g['price']:,}원</b> ({ship})\n"
    return (f"\n{head}✅ {_cat(g.get('category_name'))} {_esc(g['name'], 60)}\n"
            f"{price}{save}🔗 <a href=\"{g['url']}\">상품 보러가기</a>\n")


def live_message(hits, golds, when):
    p = [f"🔥 <b>{config.CHANNEL_TITLE}</b>\n"
         f"{_greeting(when)}  {when:%m/%d %H:%M}\n"]

    if hits:
        p.append(f"\n━━━━━━━━━━━━━\n🚨 <b>급락 포착 {len(hits)}건</b>\n")
        p += [_deal_block(h) for h in hits]

    if not hits and golds:
        p.append("\n<i>이번 시간대 급락 포착 없음 — 아래는 쿠팡 공식 당일 특가입니다.</i>\n")

    if golds:
        label = "오늘의 골드박스 특가"
        p.append(f"\n━━━━━━━━━━━━━\n⭐ <b>{label}</b>\n")
        p += [_gold_block(g) for g in golds]

    if not hits and not golds:
        return ""
    return "".join(p) + config.FOOTER


def pinned_notice():
    """채널에 한 번 올려서 상단 고정할 안내문"""
    return (f"📌 <b>{config.CHANNEL_TITLE} 안내</b>\n\n"
            "쿠팡에서 <b>평소보다 크게 싸진 상품</b>과 "
            "<b>당일 골드박스 특가</b>를 하루 4번 자동으로 올려드립니다.\n\n"
            "🕖 07:10 · 🕛 12:00 · 🕕 18:00 · 🕙 22:00\n\n"
            "<b>표시 기준</b>\n"
            "· 비교가격 = 최근 30일 평균 판매가\n"
            "· 🎁 표시 = 평소 대비 30% 이상 하락\n"
            "· 🚨 표시 = 가격 입력 오류 의심 (빠른 품절·취소 가능)\n\n"
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
