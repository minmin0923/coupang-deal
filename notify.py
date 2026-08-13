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
def live_message(hits, when):
    lines = [f"🛒 <b>쿠팡 딜</b>  {when:%m/%d %H:%M}\n"]
    for h in hits:
        off = int((1 - h["ratio"]) * 100)
        tag = "🚨 <b>가격오류 의심</b>\n" if h["grade"] == "S" else ""
        rocket = "🚀 " if h["is_rocket"] else ""
        lines.append(
            f"\n{tag}{rocket}<a href=\"{h['url']}\">{_esc(h['name'])}</a>\n"
            f"<b>{h['price']:,}원</b> <s>{h['np']:,}원</s> ({off}%↓ / {h['saving']:,}원 절약)\n"
            f"<i>{_esc(h['category_name'], 20)}</i>")
    return "".join(lines) + config.FOOTER


# ── 진단(테스트) 발송 ───────────────────────────────────────
def test_message(rows, stats, when):
    """등급/점수/괴리율/세부배점을 전부 보여줌. 임계값 조정용."""
    head = (f"🧪 <b>테스트 리포트</b>  {when:%m/%d %H:%M}\n"
            f"<code>수집 {stats['collected']} · 하드컷통과 {stats['passed']} · "
            f"네이버매칭 {stats['matched']} ({stats['match_rate']}%)</code>\n"
            f"<code>등급 S{stats['S']} A{stats['A']} B{stats['B']} "
            f"C{stats['C']} D{stats['D']}</code>\n"
            f"<code>발송컷 ratio≤{config.SEND_RATIO} / 절약≥{config.MIN_SAVING:,}</code>\n")
    lines = [head]
    for r in rows[:config.TEST_MAX_ITEMS]:
        parts = " ".join(f"{k}{v:+d}" for k, v in r["detail"].items() if v)
        lines.append(
            f"\n{BADGE.get(r['grade'],'·')} <b>{r['grade']}</b> "
            f"<code>{r['score']}점 r={r['ratio']:.2f}</code>\n"
            f"<a href=\"{r['url']}\">{_esc(r['name'], 45)}</a>\n"
            f"<code>쿠팡 {r['cp']:,} vs 기준 {r['np']:,}</code>\n"
            f"<code>{parts}</code>\n"
            f"<code>kw: {_esc(r['keyword'], 40)}</code>")

    if stats["rejects"]:
        top = sorted(stats["rejects"].items(), key=lambda x: -x[1])[:6]
        lines.append("\n\n<b>탈락 사유 TOP</b>\n" +
                     "\n".join(f"<code>{_esc(k,30)}: {v}</code>" for k, v in top))
    return "".join(lines)


def selftest_message(results):
    ok = lambda b: "✅" if b else "❌"
    return ("🔧 <b>연결 점검</b>\n" +
            "\n".join(f"{ok(v)} {k}" for k, v in results.items()))
