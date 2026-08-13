"""하드컷 -> 실효가 보정 -> 점수 -> 등급"""
import re
import config

UNIT_HINT = re.compile(r"\d+\s*(g|kg|ml|l|개|매|팩|입|정|포|장|롤)", re.I)


def hard_cut(item) -> str:
    """탈락 사유 문자열 반환. 통과하면 빈 문자열."""
    name = item["name"]
    if item["price"] < config.MIN_PRICE:
        return f"저가({item['price']})"
    if len(name) < config.MIN_NAME_LEN:
        return "상품명 짧음"
    for w in config.BLOCK_WORDS:
        if w in name:
            return f"금지어({w})"
    return ""


def effective_prices(item, p_ref):
    """배송비 보정된 양쪽 실효가"""
    cp = item["price"] + (0 if item["is_rocket"] or item["free_ship"]
                          else config.COUPANG_SHIPPING)
    np_ = p_ref * config.NAVER_SHIP_MULT
    return cp, np_


def digit_error(cp, np_) -> bool:
    """0이 하나(또는 둘) 빠진 패턴인지"""
    for mult in (10, 100):
        if 0.80 <= (cp * mult) / np_ <= 1.20:
            return True
    return False


def score(item, p_ref, in_goldbox=False):
    cp, np_ = effective_prices(item, p_ref)
    ratio  = cp / np_
    saving = int(np_ - cp)

    s, detail = 0, {}

    # A. 괴리율 (40)  ※ 중간구간이 너무 박하면 진짜 딜이 C로 떨어짐
    a = 40 if ratio <= 0.15 else 35 if ratio <= 0.30 else 29 if ratio <= 0.45 \
        else 24 if ratio <= 0.60 else 17 if ratio <= 0.70 else 7 if ratio <= 0.80 else 0
    s += a; detail["괴리율"] = a

    # B. 자릿수 오류 (20)
    b = 20 if digit_error(cp, np_) else 0
    s += b; detail["자릿수"] = b

    # C. 판매 인기도 = 리뷰수 대체 (15)
    rk = item.get("rank") or 999
    c = 15 if rk <= 10 else 11 if rk <= 30 else 7 if rk <= 60 else 3 if rk <= 100 else 0
    s += c; detail["인기도"] = c

    # D. 절대 절약액 (10)
    d = 10 if saving >= 100000 else 8 if saving >= 50000 else 5 if saving >= 20000 \
        else 2 if saving >= config.MIN_SAVING else 0
    s += d; detail["절약액"] = d

    # E. 로켓 (8)
    e = 8 if item["is_rocket"] else 0
    s += e; detail["로켓"] = e

    # F. 골드박스 (7)
    f = 7 if in_goldbox else 0
    s += f; detail["골드박스"] = f

    # 감점
    pen = 0
    if not UNIT_HINT.search(item["name"]):
        pen -= 8; detail["수량표기없음"] = -8
    if not item["is_rocket"] and not item["free_ship"] and item["price"] < 20000:
        pen -= 5; detail["배송비부담"] = -5
    s += pen

    s = max(0, min(100, s))
    return {
        "score": s, "ratio": ratio, "saving": saving,
        "cp": int(cp), "np": int(np_), "detail": detail,
        "digit_error": b == 20,
    }


def grade(res):
    """S / A / B / C / D"""
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
