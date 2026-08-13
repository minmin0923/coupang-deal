"""
캘리브레이션: 축적된 판정 로그로 '정상 상태의 괴리율 분포'를 본다.
임계값은 감이 아니라 이 히스토그램을 보고 정한다.

  python calibrate.py            전체
  python calibrate.py 식품        카테고리 지정
"""
import sys, collections, statistics
import store

BINS = [(0.0, .10), (.10, .20), (.20, .30), (.30, .40), (.40, .50),
        (.50, .60), (.60, .70), (.70, .80), (.80, .90), (.90, 1.00),
        (1.00, 1.20), (1.20, 99)]


def histogram(ratios, width=48):
    counts = [sum(1 for r in ratios if lo <= r < hi) for lo, hi in BINS]
    peak = max(counts) or 1
    print(f"\n{'구간':<14}{'건수':>6}")
    for (lo, hi), c in zip(BINS, counts):
        bar = "█" * int(width * c / peak)
        label = f"{lo:.2f}~{hi:.2f}" if hi < 99 else f"{lo:.2f}+"
        print(f"{label:<14}{c:>6}  {bar}")


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    i = min(int(len(sorted_vals) * p / 100), len(sorted_vals) - 1)
    return sorted_vals[i]


def main():
    cat = sys.argv[1] if len(sys.argv) > 1 else None
    con = store.connect()

    q = "SELECT category_name, ratio FROM judgments WHERE ratio IS NOT NULL"
    args = ()
    if cat:
        q += " AND category_name LIKE ?"; args = (f"%{cat}%",)
    rows = con.execute(q, args).fetchall()

    if not rows:
        print("판정 로그가 비어 있습니다. test 또는 dry 모드로 먼저 며칠 돌리세요.")
        return

    ratios = sorted(r[1] for r in rows)
    print(f"표본 {len(ratios)}건" + (f" / 카테고리: {cat}" if cat else ""))
    print(f"중앙값 {statistics.median(ratios):.3f}   "
          f"평균 {statistics.mean(ratios):.3f}")
    histogram(ratios)

    print("\n── 퍼센타일 (낮을수록 싼 것) ──")
    for p in (1, 3, 5, 10, 25, 50):
        print(f"  하위 {p:>2}%  ratio ≤ {percentile(ratios, p):.3f}")

    print("\n── 카테고리별 권장 발송컷 (하위 5%) ──")
    per = collections.defaultdict(list)
    for c, r in rows:
        per[c or "미상"].append(r)
    for c, vs in sorted(per.items(), key=lambda x: -len(x[1])):
        if len(vs) < 30:
            print(f"  {c:<12} 표본 {len(vs):>4}건 — 부족, 판단 보류")
            continue
        vs.sort()
        print(f"  {c:<12} 표본 {len(vs):>4}건  중앙 {statistics.median(vs):.2f}  "
              f"→ 권장컷 {percentile(vs, 5):.2f}")

    print("\n해석: '정상 상태 봉우리'의 왼쪽 꼬리가 진짜 딜입니다.")
    print("      config.SEND_RATIO 를 위 권장컷 근처로 맞추세요.")
    con.close()


if __name__ == "__main__":
    main()
