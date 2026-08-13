"""
쿠팡 딜 봇 메인

사용법:
  python main.py selftest   쿠팡/네이버/텔레그램 연결만 점검
  python main.py test       수집 -> 판정 -> 진단리포트를 텔레그램으로 (실발송 아님)
  python main.py dry        수집 -> 판정 -> 콘솔 출력만
  python main.py live       수집 -> 판정 -> 실제 딜 발송
"""
import sys, time, collections
from datetime import datetime, timedelta, timezone

import config, coupang, naver, scoring, store, notify

KST = timezone(timedelta(hours=9))
GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}


def collect(con, at, verbose=True):
    all_items, gold_ids = [], set()

    for cid, cname in config.CATEGORIES.items():
        raw = coupang.best_category(cid, limit=config.PER_CATEGORY)
        items = []
        for i, x in enumerate(raw, start=1):
            n = coupang.normalize(x, cid)
            n["rank"] = x.get("rank") or i
            n["free_ship"] = bool(x.get("isFreeShipping"))
            n["category_name"] = n["category_name"] or cname
            items.append(n)
        store.upsert(con, items, at)
        all_items += items
        if verbose:
            print(f"  {cname}: {len(items)}건")
        time.sleep(1.5)

    gold_items = []
    for x in coupang.goldbox():
        n = coupang.normalize(x)
        n["rank"] = 5                      # 골드박스는 인기 상위로 간주
        n["free_ship"] = bool(x.get("isFreeShipping"))
        gold_ids.add(n["product_id"])
        gold_items.append(n)
    store.upsert(con, gold_items, at)
    all_items += gold_items
    if verbose:
        print(f"  골드박스: {len(gold_items)}건")

    dedup = {}
    for it in all_items:
        dedup.setdefault(it["product_id"], it)
    return list(dedup.values()), gold_ids


def judge(con, items, gold_ids, at, budget=250):
    """하드컷 -> 네이버 기준가 -> 점수 -> 등급. budget = 네이버 호출 상한"""
    rows, rejects = [], collections.Counter()
    calls = 0

    for it in items:
        reason = scoring.hard_cut(it)
        if reason:
            rejects[reason] += 1
            continue
        if calls >= budget:
            rejects["API예산소진"] += 1
            continue

        p_ref, diag = naver.reference_price(it["name"])
        calls += 1
        time.sleep(0.12)

        base_log = {
            "product_id": it["product_id"], "captured_at": at, "name": it["name"],
            "category_name": it["category_name"], "price": it["price"],
            "keyword": diag["keyword"],
        }

        if p_ref is None:
            rejects[diag["reason"] or "매칭실패"] += 1
            store.log_judgment(con, {**base_log, "p_ref": None, "ratio": None,
                                     "saving": None, "score": None, "grade": None,
                                     "digit_error": 0,
                                     "reject_reason": diag["reason"]})
            continue

        res = scoring.score(it, p_ref, it["product_id"] in gold_ids)
        g = scoring.grade(res)
        rows.append({**it, **res, "grade": g, "p_ref": int(p_ref),
                     "keyword": diag["keyword"]})
        store.log_judgment(con, {**base_log, "p_ref": int(p_ref),
                                 "ratio": round(res["ratio"], 4),
                                 "saving": res["saving"], "score": res["score"],
                                 "grade": g, "digit_error": int(res["digit_error"]),
                                 "reject_reason": ""})

    con.commit()
    rows.sort(key=lambda r: (-GRADE_ORDER[r["grade"]], -r["score"]))
    return rows, rejects, calls


def build_stats(items, rows, rejects, calls):
    g = collections.Counter(r["grade"] for r in rows)
    return {
        "collected": len(items),
        "passed": calls,
        "matched": len(rows),
        "calls": calls,
        "match_rate": round(100 * len(rows) / max(calls, 1)),
        "S": g["S"], "A": g["A"], "B": g["B"], "C": g["C"], "D": g["D"],
        "rejects": dict(rejects),
    }


def run_selftest():
    res = {}
    try:
        res["쿠팡 파트너스 API"] = len(coupang.best_category(1014, limit=3)) > 0
    except Exception as e:
        print("쿠팡:", e); res["쿠팡 파트너스 API"] = False
    try:
        res["네이버 쇼핑 API"] = len(naver.search("삼다수 2L 24개", display=3)) > 0
    except Exception as e:
        print("네이버:", e); res["네이버 쇼핑 API"] = False
    res["텔레그램"] = True
    msg = notify.selftest_message(res)
    print(msg)
    notify.send(msg)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    now = datetime.now(KST)
    at = now.strftime("%Y-%m-%d %H:%M:%S")

    if mode == "selftest":
        run_selftest()
        return

    con = store.connect()
    print(f"[{at}] mode={mode}")
    items, gold = collect(con, at)
    print(f"수집 {len(items)}건 (중복제거 후)")

    rows, rejects, calls = judge(con, items, gold, at)
    stats = build_stats(items, rows, rejects, calls)
    print(f"네이버 호출 {calls} / 매칭 {stats['matched']} ({stats['match_rate']}%)")
    print("등급:", {k: stats[k] for k in "SABCD"})
    print("탈락:", dict(sorted(rejects.items(), key=lambda x: -x[1])[:8]))

    if mode == "test":
        notify.send(notify.test_message(rows, stats, now))

    elif mode == "live":
        if stats["S"] > config.MAX_S_PER_DAY:
            notify.send(f"⚠️ S등급 {stats['S']}건 — 로직 오류 의심. 발송 중단.")
            con.close(); return
        hits = [r for r in rows if r["grade"] in ("S", "A", "B")
                and not store.recently_sent(con, r["product_id"], r["price"], at)]
        hits = hits[:config.MAX_ALERTS]
        if hits and notify.send(notify.live_message(hits, now)):
            for h in hits:
                store.mark_sent(con, h["product_id"], h["price"], at)
        print(f"발송 {len(hits)}건")

    else:
        for r in rows[:20]:
            print(f"  {r['grade']} {r['score']:3d}점 r={r['ratio']:.2f} "
                  f"{r['cp']:>8,} vs {r['np']:>8,}  {r['name'][:40]}")

    con.close()


if __name__ == "__main__":
    main()
