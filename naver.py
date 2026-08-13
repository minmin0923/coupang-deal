"""네이버 쇼핑 검색 API + 검색어 추출 (이 프로젝트 성패를 가르는 부분)"""
import os, re, time, statistics
from urllib.parse import quote
import requests

import config

CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
ENDPOINT = "https://openapi.naver.com/v1/search/shop.json"

# 쿠팡 상품명에 붙는 마케팅 잡음
NOISE = [
    "로켓배송", "로켓프레시", "로켓직구", "로켓설치", "무료배송", "당일발송",
    "오늘출발", "새벽배송", "상세설명 참조", "상세페이지 참조", "본상품선택",
    "공식판매처", "공식스토어", "정품", "사은품", "증정", "단독특가",
    "최신형", "신상품", "인기", "베스트", "택1", "골라담기",
]
JUNK = {"x", "X", "및", "외", "등", "형", "용", "총"}
VOL_RE   = re.compile(r"\d+(?:\.\d+)?\s*(?:kg|g|ml|mL|L|l|리터|cc)\b", re.I)
COUNT_RE = re.compile(r"(\d+)\s*(개입|개|매|팩|입|정|포|캡슐|롤|장|구)\b")


def _quantity_hints(full_name: str):
    """상품명 '전체'에서 용량/수량을 뽑는다.

    쉼표 뒤에 있는 '24개' 같은 묶음 수량을 놓치면 기준가가 24배 어긋난다.
    이 프로젝트에서 가장 자주 터지는 버그라 별도 함수로 분리.
    """
    vol = None
    m = VOL_RE.search(full_name)
    if m:
        vol = m.group(0).replace(" ", "")

    cnt = None
    best = 1
    for num, unit in COUNT_RE.findall(full_name):
        try:
            v = int(num)
        except ValueError:
            continue
        if v > best:                    # '1개'는 정보가 없음
            best, cnt = v, f"{v}{unit}"
    return vol, cnt


def extract_keyword(name: str, max_words: int = 7) -> str:
    """쿠팡 상품명 -> 네이버 검색어.

    예) '곰곰 매일 아침 삼다수 2L, 24개' -> '곰곰 매일 아침 삼다수 2L 24개'
    """
    vol, cnt = _quantity_hints(name)          # 자르기 전에 먼저 확보

    s = re.sub(r"\[[^\]]*\]", " ", name)      # [대괄호] 제거
    s = re.sub(r"\([^)]*\)", " ", s)          # (소괄호) 제거
    s = s.split(",")[0]                        # 첫 쉼표 앞까지
    for w in NOISE:
        s = s.replace(w, " ")
    s = re.sub(r"[^0-9A-Za-z가-힣.]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    out = []
    for w in s.split():
        if len(out) >= max_words:
            break
        if w in JUNK:
            continue
        out.append(w)

    joined = " ".join(out)
    if vol and vol.lower() not in joined.lower().replace(" ", ""):
        out.append(vol)
    if cnt and cnt not in joined:
        out.append(cnt)
    return " ".join(out)


def search(keyword: str, display: int = 20, retries: int = 2):
    if not CLIENT_ID:
        return []
    url = f"{ENDPOINT}?query={quote(keyword)}&display={display}&sort=asc"
    for i in range(retries):
        try:
            r = requests.get(url, timeout=15, headers={
                "X-Naver-Client-Id": CLIENT_ID,
                "X-Naver-Client-Secret": CLIENT_SECRET,
            })
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status()
            return r.json().get("items", [])
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"[NAVER ERR] {keyword}: {e}")
                return []
            time.sleep(2)
    return []


def reference_price(product_name: str):
    """기준가 산출.

    반환: (P_ref, 진단dict) — 실패 시 (None, 사유 포함 dict)
    """
    kw = extract_keyword(product_name)
    diag = {"keyword": kw, "n_all": 0, "n_used": 0, "reason": ""}
    if len(kw) < 4:
        diag["reason"] = "검색어 추출 실패"
        return None, diag

    items = search(kw)
    diag["n_all"] = len(items)
    if not items:
        diag["reason"] = "네이버 결과 0건"
        return None, diag

    catalog, store = [], []
    for it in items:
        if str(it.get("productType", "")) not in config.NAVER_KEEP_TYPES:
            continue                                   # 중고/단종/판매예정 제외
        if "쿠팡" in (it.get("mallName") or ""):
            continue                                   # 자기 자신 제외
        try:
            p = int(it["lprice"])
        except (KeyError, ValueError):
            continue
        if p <= 0:
            continue
        # 가격비교 카탈로그(1)와 개별 스토어(2,3) 분리
        (catalog if str(it.get("productType")) == "1" else store).append(p)

    pool = catalog if len(catalog) >= config.NAVER_MIN_SAMPLES else catalog + store
    diag["n_used"] = len(pool)
    if len(pool) < config.NAVER_MIN_SAMPLES:
        diag["reason"] = f"유효 표본 부족({len(pool)})"
        return None, diag

    if max(pool) / max(min(pool), 1) >= config.NAVER_SPREAD_LIMIT:
        diag["reason"] = "가격 산포 과다(옵션가 착시 의심)"
        return None, diag

    # 카탈로그 vs 스마트스토어 교차검증
    if catalog and store:
        c_med, s_med = statistics.median(catalog), statistics.median(store)
        if max(c_med, s_med) / max(min(c_med, s_med), 1) >= config.STORE_GAP_LIMIT:
            diag["reason"] = "카탈로그-스토어 괴리 과다"
            return None, diag

    p_ref = statistics.median(pool)
    diag["p_ref"] = int(p_ref)
    diag["n_catalog"], diag["n_store"] = len(catalog), len(store)
    return p_ref, diag
