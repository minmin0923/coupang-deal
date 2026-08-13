"""
쿠팡 딜 봇 v3 — 기준가 = 쿠팡 자체 가격 이력

사용법:
  python main.py selftest   쿠팡/텔레그램 연결 점검
  python main.py notice     채널 안내문 1회 발송 (고정용)
  python main.py debug      쿠팡 API 원본 응답 출력 (필드명 확인용)
  python main.py test       수집 -> 판정 -> 진단리포트 텔레그램 발송
  python main.py dry        콘솔 출력만
  python main.py live       데일리 발송 (급락 + 골드박스)
  python main.py hunt       급락 감시 전용 (있을 때만 발송)
  python main.py auto       스케줄 1회 실행 — 시각 보고 데일리/급락 선택
  python main.py watch      ★ 상주 감시 — 러너를 켜둔 채 몇 분마다 계속 순회
"""
import sys, time, collections, subprocess, traceback
from datetime import datetime, timedelta, timezone

import config, coupang, scoring, store, notify

KST = timezone(timedelta(hours=9))
GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}


def fetch(verbose=True, cats=None, per=None, with_gold=True):
    """수집만. DB 저장은 판정 뒤에 한다(오늘 값이 기준가에 섞이지 않게)."""
    items, gold_ids = [], set()

    for cid, cname in (cats or config.CATEGORIES).items():
        raw = coupang.best_category(cid, limit=per or config.PER_CATEGORY)
        for i, x in enumerate(raw, start=1):
            n = coupang.normalize(x, cid)
            n["rank"] = x.get("rank") or i
            n["category_name"] = n["category_name"] or cname
            items.append(n)
        if verbose:
            print(f"  {cname}: {len(raw)}건")
        time.sleep(config.CATEGORY_WAIT)

    golds = []
    for x in (coupang.goldbox() if with_gold else []):
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


def send_cards(con, hits, golds, now, at, header=None):
    """대표 1건만 썸네일 카드, 나머지는 한 메시지로 묶어 발송."""
    if not hits and not golds:
        return 0
    pool = hits + golds
    # 대표 = 급락 우선, 그중 점수/판매순위가 가장 좋은 것
    star = min(pool, key=lambda x: (0 if x in hits else 1, x.get("rank") or 999))

    notify.send(header or notify.header_message(len(hits), len(golds), now))
    time.sleep(0.4)

    cap = notify.deal_caption(star) if star in hits else notify.gold_caption(star)
    notify.send_photo(star.get("image"), cap)
    time.sleep(0.4)

    rest = notify.list_message(hits, golds, now, skip_id=star["product_id"])
    if rest:
        notify.send(rest)

    for x in pool:
        store.mark_sent(con, x["product_id"], x["price"], at)
    return len(pool)


def run_hunt(con, now, at):
    """급락 감시 전용 — 있을 때만 발송, 없으면 조용히 종료."""
    items, gold_ids, _ = fetch(cats=config.HUNT_CATEGORIES,
                               per=config.HUNT_PER_CATEGORY, with_gold=False)
    print(f"수집 {len(items)}건")

    rows, rejects = judge(con, items, gold_ids, at)
    store.upsert(con, items, store.hour_bucket(at))   # 시간당 1건만 적재
    store.prune(con, config.PRUNE_DAYS)

    hits = [r for r in rows if r["grade"] in ("S", "A")
            and not store.recently_sent(con, r["product_id"], r["price"], at)]
    hits = hits[:config.HUNT_MAX_CARDS]

    warming = sum(v for k, v in rejects.items() if k.startswith("이력축적중"))
    print(f"판정 {len(rows)} / 이력축적중 {warming} / 급락 {len(hits)}")

    if not hits:
        print("급락 없음 — 발송 안 함")
        return
    send_cards(con, hits, [], now, at, header=notify.hunt_header(len(hits), now))


def attach_ref(con, items):
    """모든 상품에 '비교가'를 붙인다. 못 붙이는 상품은 버린다.
       우선순위: 쿠팡 원가 > 자체 이력 최고가

       가격비교 없는 카드는 발송하지 않는다는 원칙을 여기서 강제한다.
    """
    out, dropped = [], 0
    for it in items:
        price = it["price"]
        ref, label = 0, ""

        api_orig = it.get("orig_price") or 0
        rate = it.get("discount_rate") or 0
        if api_orig > price:
            ref, label = api_orig, "정상가"
        else:
            hist, n = store.ref_price(con, it["product_id"],
                                      config.BASELINE_DAYS, config.REF_MIN_POINTS)
            if hist and hist > price:
                ref, label = hist, "최근 최고가"
            elif rate > 0:                       # 최후: 할인율로 원가 역산
                ref, label = round(price / (1 - rate / 100)), "정상가"

        if not ref or (ref - price) / ref < config.REF_MIN_GAP:
            dropped += 1
            continue

        it["ref"] = int(ref)
        it["ref_label"] = label
        it["off"] = int(round((1 - price / ref) * 100))
        it["cut"] = int(ref - price)

        # 인기 급상승 — 판매 순위가 크게 뛴 상품
        cur = it.get("rank") or 999
        prev, jump = store.rank_rise(con, it["product_id"], cur, config.RISE_DAYS)
        it["rising"] = bool(prev and cur <= config.RISE_MAX_RANK
                            and jump >= config.RISE_MIN_JUMP)
        it["rank_prev"], it["rank_jump"] = prev, jump
        out.append(it)
    return out, dropped


def slot_due(con, now):
    """지금이 정기 발송 슬롯인지. 이미 보낸 슬롯이면 False."""
    cur = now.hour * 60 + now.minute
    for h, mi in config.DAILY_SLOTS:
        start = h * 60 + mi
        if start <= cur < start + config.SLOT_WINDOW:
            key = f"{now:%Y-%m-%d}-{h:02d}{mi:02d}"
            if store.get_meta(con, "last_slot") != key:
                return key
    return None


def git_sync(tag=""):
    """루프 도중 이력을 커밋. 러너가 죽어도 데이터가 남게 한다."""
    try:
        subprocess.run(["git", "config", "user.name", "deal-bot"], check=False)
        subprocess.run(["git", "config", "user.email",
                        "deal-bot@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", "-A", "data/"], check=False)
        r = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if r.returncode != 0:
            subprocess.run(["git", "commit", "-m", f"watch {tag}"], check=False)
            subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)
            subprocess.run(["git", "push"], check=False)
            print("  [git] 이력 저장됨")
    except Exception as e:
        print("  [git] 실패:", e)


def one_cycle(con, now, at):
    """1회 순회. 정기 슬롯이면 종합 발송, 아니면 급락만 실시간 발송."""
    slot = slot_due(con, now)

    # ── 정기 발송 (하루 3타임) ──────────────────────────
    if slot:
        print(f"[{at}] 정기 발송 슬롯 {slot}")
        items, gold_ids, golds = fetch(verbose=False)
        rows, _ = judge(con, items, gold_ids, at)
        store.upsert(con, items, store.hour_bucket(at))
        store.record_ranks(con, items, store.hour_bucket(at))

        hits = [r for r in rows if r["grade"] in ("S", "A", "B")
                and not store.recently_sent(con, r["product_id"], r["price"], at)]
        hits, _ = attach_ref(con, hits)
        hits = hits[:config.MAX_ALERTS]
        hit_ids = {h["product_id"] for h in hits}

        # 핫딜 섹션: 추적 중인 인기상품 전체에서 '자기 과거보다 싸진 것'을 고른다.
        # (골드박스만 보면 원가를 알 수 없어 늘 비어버림 — 실측으로 확인됨)
        cand = [it for it in items
                if it["product_id"] not in hit_ids and not scoring.hard_cut(it)]
        priced, dropped = attach_ref(con, cand)
        priced = [g for g in priced
                  if g["off"] >= config.DAILY_MIN_OFF and g["cut"] >= config.DAILY_MIN_CUT]
        priced.sort(key=lambda g: (not g.get("rising"), -g["off"]))
        fresh = [g for g in priced
                 if not store.recently_sent(con, g["product_id"], g["price"], at)]
        picks = (fresh or priced)[:config.GOLDBOX_SHOW]

        print(f"  급락 {len(hits)} / 핫딜 {len(picks)} · 후보 {len(cand)} · 비교가없음 {dropped}")
        send_cards(con, hits, picks, now, at)
        store.set_meta(con, "last_slot", slot)
        return True

    # ── 실시간 급락 감시 ────────────────────────────────
    items, gold_ids, golds = fetch(verbose=False, cats=config.HUNT_CATEGORIES,
                                   per=config.HUNT_PER_CATEGORY, with_gold=True)
    rows, rejects = judge(con, items, gold_ids, at)
    store.upsert(con, items, store.hour_bucket(at))   # 핫딜 상품도 이력 적재
    store.record_ranks(con, items, store.hour_bucket(at))

    hits = [r for r in rows if r["grade"] in ("S", "A")
            and not store.recently_sent(con, r["product_id"], r["price"], at)]
    hits, no_ref = attach_ref(con, hits)
    hits = hits[:config.HUNT_MAX_CARDS]

    warming = sum(v for k, v in rejects.items() if k.startswith("이력축적중"))
    print(f"[{at}] 수집 {len(items)} · 판정 {len(rows)} · 축적중 {warming} "
          f"· 급락 {len(hits)}" + (f" (비교가없음 {no_ref})" if no_ref else ""))

    if hits:
        send_cards(con, hits, [], now, at,
                   header=notify.hunt_header(len(hits), now))
        print(f"  🚨 급락 {len(hits)}건 즉시 발송")
        return True
    return False


def run_watch(con):
    """러너를 켜둔 채 계속 순회. 급락이 뜨는 즉시 알린다."""
    started = datetime.now(KST)
    deadline = started + timedelta(minutes=config.WATCH_MINUTES)
    print(f"상주 감시 시작 — {config.WATCH_INTERVAL_SEC}초 간격, "
          f"{config.WATCH_MINUTES}분간 (~{deadline:%H:%M})")

    n = 0
    while datetime.now(KST) < deadline:
        n += 1
        now = datetime.now(KST)
        at = now.strftime("%Y-%m-%d %H:%M:%S")
        try:
            one_cycle(con, now, at)
        except Exception:
            print("  [오류] 이번 순회 건너뜀")
            traceback.print_exc()

        if n % config.WATCH_SYNC_EVERY == 0:
            store.prune(con, config.PRUNE_DAYS)
            git_sync(f"{now:%m-%d %H:%M}")

        remain = (deadline - datetime.now(KST)).total_seconds()
        if remain <= 0:
            break
        time.sleep(min(config.WATCH_INTERVAL_SEC, remain))

    store.prune(con, config.PRUNE_DAYS)
    git_sync("final")
    print(f"상주 감시 종료 — 총 {n}회 순회")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
    now = datetime.now(KST)
    at = now.strftime("%Y-%m-%d %H:%M:%S")

    if mode == "selftest":
        run_selftest()
        return

    if mode == "debug":
        # 쿠팡 API 원본 응답을 그대로 출력 — 원가/할인율 필드명 확인용
        import json
        print("=" * 60)
        print("[골드박스 원본 응답]")
        gb = coupang.goldbox()
        print(f"총 {len(gb)}건")
        for x in gb[:2]:
            print(json.dumps(x, ensure_ascii=False, indent=2))
        if gb:
            print("\n>> 골드박스 필드 목록:", sorted(gb[0].keys()))
        print("=" * 60)
        print("[베스트카테고리(생활용품) 원본 응답]")
        bc = coupang.best_category(1014, limit=2)
        for x in bc[:2]:
            print(json.dumps(x, ensure_ascii=False, indent=2))
        if bc:
            print("\n>> 베스트 필드 목록:", sorted(bc[0].keys()))
        print("=" * 60)
        return

    if mode == "notice":
        # 채널 안내문 발송 -> 텔레그램에서 길게 눌러 '고정'하면 끝
        notify.send(notify.pinned_notice())
        print("안내문 발송 완료. 텔레그램에서 해당 메시지를 고정하세요.")
        return

    con = store.connect()

    if mode == "watch":
        print(f"[{at}] mode=watch")
        run_watch(con)
        con.close()
        return

    if mode == "auto":
        # 데일리 시각의 첫 실행(정시~19분)이면 전체 발송, 아니면 급락 감시만
        mode = "live" if slot_due(con, now) else "hunt"
        print(f"[auto] {now:%H:%M} -> {mode}")

    if mode == "hunt":
        print(f"[{at}] mode=hunt")
        run_hunt(con, now, at)
        con.close()
        return

    print(f"[{at}] mode={mode}")

    items, gold, golds = fetch()
    print(f"수집 {len(items)}건 (중복제거 후)")

    rows, rejects = judge(con, items, gold, at)      # 저장 전에 판정
    store.upsert(con, items, store.hour_bucket(at))   # 그다음 오늘 값 적재
    store.prune(con, config.PRUNE_DAYS)

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

        send_cards(con, hits, picks, now, at)
        print(f"발송 — 급락 {len(hits)}건 / 골드박스 {len(picks)}건")

    else:
        for r in rows[:20]:
            print(f"  {r['grade']} {r['score']:3d}점 r={r['ratio']:.2f} "
                  f"{r['cp']:>8,} vs {r['np']:>8,} (n={r['n_points']})  {r['name'][:36]}")

    con.close()


if __name__ == "__main__":
    main()
