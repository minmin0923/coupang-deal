"""
쿠팡 딜 봇 v3 — 기준가 = 쿠팡 자체 가격 이력

사용법:
  python main.py selftest   쿠팡/텔레그램 연결 점검
  python main.py test       수집 -> 판정 -> 진단리포트 텔레그램 발송
  python main.py dry        콘솔 출력만
  python main.py live       실제 딜 발송
"""
import sys, time, collections
from datetime import datetime, timedelta, timezone

import config, coupang, scoring, store, notify

KST = timezone(timedelta(hours=9))
GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}


def fetch(verbose=True):
    """수집만. DB 저장은 판정 뒤에 한다(오늘 값이 기준가에 섞이지 않게)."""
    items, gold_ids = [], set()

    for cid, cname in config.CATEGORIES.items():
        raw = coupang.best_category(cid, limit=config.PER_CATEGORY)
        for i, x in enumerate(raw, start=1):
            n = coupang.normalize(x, cid)
            n["rank"] = x.get("rank") or i
            n["category_name"] = n["category_name"] or cname
            items.append(n)
        if verbose:
            print(f"  {cname}: {len(raw)}건")
        time.sleep(1.5)

    golds = []
    for x in coupang.goldbox():
        n = coupang.normalize(x)
        n["rank"] = 5
        n["category_name"] = n["category_name"] or "특가"
        gold_ids.add(n["product_id"])
        items.append(n); golds.append(n)
    if verbose:
        print(f"  골드박스: {len(golds)}건")

    dedup = {}
    for it in items:
        dedup.setdefault(it["product_id"], it)
    return list(dedup.values()), gold_ids, golds


def judge(con, items, gold_ids, at):
    """자체 이력으로 판정. 외부 API 호출 없음."""
    rows, rejects = [], collections.Counter()

    for it in items:
        reason = scoring.hard_cut(it)
        if reason:
            rejects[reason] += 1
            continue

        base, n_pts = store.baseline_with_count(
            con, it["product_id"], config.BASELINE_DAYS, config.BASELINE_MIN_POINTS)
        if base is None:
            rejects[f"이력축적중({n_pts}/{config.BASELINE_MIN_POINTS})"] += 1
            continue

        res = scoring.score(it, base, n_pts, it["product_id"] in gold_ids)
        g = scoring.grade(res)
        rows.append({**it, **res, "grade": g})

        store.log_judgment(con, {
            "product_id": it["product_id"], "captured_at": at, "name": it["name"],
            "category_name": it["category_name"], "price": it["price"],
            "p_ref": int(base), "ratio": round(res["ratio"], 4),
            "saving": res["saving"], "score": res["score"], "grade": g,
            "digit_error": int(res["digit_error"]),
            "keyword": f"n={n_pts}", "reject_reason": ""})

    con.commit()
    rows.sort(key=lambda r: (-GRADE_ORDER[r["grade"]], -r["score"]))
    return rows, rejects


def build_stats(items, rows, rejects):
    g = collections.Counter(r["grade"] for r in rows)
    warming = sum(v for k, v in rejects.items() if k.startswith("이력축적중"))
    return {
        "collected": len(items), "matched": len(rows), "warming": warming,
        "S": g["S"], "A": g["A"], "B": g["B"], "C": g["C"], "D": g["D"],
        "rejects": dict(rejects),
    }


def run_selftest():
    res = {}
    try:
        res["쿠팡 파트너스 API"] = len(coupang.best_category(1014, limit=3)) > 0
    except Exception as e:
        print("쿠팡:", e); res["쿠팡 파트너스 API"] = False
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

    items, gold, golds = fetch()
    print(f"수집 {len(items)}건 (중복제거 후)")

    rows, rejects = judge(con, items, gold, at)      # 저장 전에 판정
    store.upsert(con, items, at)                      # 그다음 오늘 값 적재

    stats = build_stats(items, rows, rejects)
    print(f"판정 {stats['matched']}건 / 이력축적중 {stats['warming']}건")
    print("등급:", {k: stats[k] for k in "SABCD"})
    print("탈락:", dict(sorted(rejects.items(), key=lambda x: -x[1])[:6]))

    if mode == "test":
        notify.send(notify.test_message(rows, stats, now))

    elif mode == "live":
        if stats["S"] > config.MAX_S_PER_DAY:
            notify.send(f"⚠️ S등급 {stats['S']}건 — 로직 오류 의심. 발송 중단.")
            con.close(); return
        hits = [r for r in rows if r["grade"] in ("S", "A", "B")
                and not store.recently_sent(con, r["product_id"], r["price"], at)]
        hits = hits[:config.MAX_ALERTS]
        hit_ids = {h["product_id"] for h in hits}

        # 골드박스로 채우기 — 24시간 내 올린 건 건너뛰어 자동 로테이션
        picks = [g for g in golds
                 if g["product_id"] not in hit_ids
                 and g["price"] >= config.MIN_PRICE
                 and not scoring.hard_cut(g)
                 and not store.recently_sent(con, g["product_id"], g["price"], at)]
        if not picks:      # 24시간 필터로 다 걸러졌으면 채널이 비지 않게 재사용
            picks = [g for g in golds if g["product_id"] not in hit_ids
                     and not scoring.hard_cut(g)][:4]
        picks = picks[:config.GOLDBOX_SHOW]

        msg = notify.live_message(hits, picks, now)
        if msg and notify.send(msg):
            for x in hits + picks:
                store.mark_sent(con, x["product_id"], x["price"], at)
        print(f"발송 — 급락 {len(hits)}건 / 골드박스 {len(picks)}건")

    else:
        for r in rows[:20]:
            print(f"  {r['grade']} {r['score']:3d}점 r={r['ratio']:.2f} "
                  f"{r['cp']:>8,} vs {r['np']:>8,} (n={r['n_points']})  {r['name'][:36]}")

    con.close()


if __name__ == "__main__":
    main()
