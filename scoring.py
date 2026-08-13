"""하드컷 -> 자체 이력 기준가 대비 점수 -> 등급
   v3: 배송비 보정 불필요(같은 상품끼리 비교), 표본 신뢰도 반영"""
import re
import config

UNIT_HINT = re.compile(r"\d+\s*(g|kg|ml|l|개|매|팩|입|정|포|장|롤)", re.I)


def hard_cut(item) -> str:
    """탈락 사유. 통과하면 빈 문자열."""
    name = item["name"]
    if item["price"] < config.MIN_PRICE:
        return f"저가(<{config.MIN_PRICE})"
    if len(name) < config.MIN_NAME_LEN:
        return "상품명 짧음"
    for w in config.BLOCK_WORDS:
        if w in name:
            return f"금지어({w})"
    return ""


def digit_error(price, base) -> bool:
    """0이 하나(또는 둘) 빠진 패턴"""
    for mult in (10, 100):
        if 0.80 <= (price * mult) / base <= 1.20:
            return True
    return False


def score(item, base, n_points, in_goldbox=False):
    """base = 자기 자신의 과거 가격 중앙값, n_points = 표본 개수"""
    price  = item["price"]
    ratio  = price / base
    drop   = int(base - price)

    s, detail = 0, {}

    # A. 하락폭 (40)
    a = 40 if ratio <= 0.15 else 35 if ratio <= 0.30 else 29 if ratio <= 0.45 \
        else 24 if ratio <= 0.60 else 17 if ratio <= 0.70 else 7 if ratio <= 0.85 else 0
    s += a; detail["하락폭"] = a

    # B. 자릿수 오류 (20)
    b = 20 if digit_error(price, base) else 0
    s += b; detail["자릿수"] = b

    # C. 판매 인기도 = 리뷰수 대체 (15)
    rk = item.get("rank") or 999
    c = 15 if rk <= 10 else 11 if rk <= 30 else 7 if rk <= 60 else 3 if rk <= 100 else 0
    s += c; detail["인기도"] = c

    # D. 절대 하락액 (10)
    d = 10 if drop >= 100000 else 8 if drop >= 50000 else 5 if drop >= 20000 \
        else 2 if drop >= config.MIN_SAVING else 0
    s += d; detail["하락액"] = d

    # E. 로켓 (8) — 직매입이라 취소 확률 낮음
    e = 8 if item["is_rocket"] else 0
    s += e; detail["로켓"] = e

    # F. 골드박스 (7)
    f = 7 if in_goldbox else 0
    s += f; detail["골드박스"] = f

    # 감점: 표본이 적으면 기준가를 못 믿는다
    if n_points < config.BASELINE_SOLID:
        s -= 12; detail["표본부족"] = -12
    if not UNIT_HINT.search(item["name"]):
        s -= 8;  detail["수량표기없음"] = -8

    s = max(0, min(100, s))
    return {
        "score": s, "ratio": ratio, "saving": drop,
        "cp": price, "np": int(base), "n_points": n_points,
        "detail": detail, "digit_error": b == 20,
    }


def grade(res):
    sc, ratio, saving = res["score"], res["ratio"], res["saving"]
    sendable = ratio <= config.SEND_RATIO and saving >= config.MIN_SAVING

    if sc >= config.GRADE_S and res["digit_error"]:
        return "S"
    if not sendable:
        return "C" if sc >= config.GRADE_C else "D"
    if sc >= config.GRADE_A:
        return "A"
    if sc >= config.GRADE_B:
        return "B"
    if sc >= config.GRADE_C:
        return "C"
    return "D"
