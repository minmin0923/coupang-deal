"""쿠팡 파트너스 Open API 클라이언트 (HMAC 인증)"""
import hmac, hashlib, os, time, json
from datetime import datetime, timezone
from urllib.parse import urlencode
import requests

DOMAIN = "https://api-gateway.coupang.com"
ACCESS_KEY = os.environ.get("COUPANG_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("COUPANG_SECRET_KEY", "")


def _auth_header(method: str, url_path: str) -> str:
    """쿠팡 HMAC 서명 생성. url_path는 쿼리스트링 포함."""
    path, _, query = url_path.partition("?")
    dt = datetime.now(timezone.utc).strftime("%y%m%dT%H%M%SZ")
    message = dt + method + path + query
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return (
        f"CEA algorithm=HmacSHA256, access-key={ACCESS_KEY}, "
        f"signed-date={dt}, signature={signature}"
    )


def _get(url_path: str, retries: int = 3):
    for attempt in range(retries):
        try:
            r = requests.get(
                DOMAIN + url_path,
                headers={
                    "Authorization": _auth_header("GET", url_path),
                    "Content-Type": "application/json;charset=UTF-8",
                },
                timeout=20,
            )
            if r.status_code == 429:          # rate limit
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f"[ERROR] {url_path} -> {e}")
                return None
            time.sleep(3 * (attempt + 1))
    return None


def best_category(category_id: int, limit: int = 100):
    """카테고리별 베스트 상품 (= 소비 많은 제품)"""
    path = (
        "/v2/providers/affiliate_open_api/apis/openapi/v1/products/"
        f"bestcategories/{category_id}?{urlencode({'limit': limit})}"
    )
    data = _get(path)
    return (data or {}).get("data", []) or []


def goldbox():
    """골드박스 (매일 오전 갱신되는 특가)"""
    path = "/v2/providers/affiliate_open_api/apis/openapi/v1/products/goldbox"
    data = _get(path)
    return (data or {}).get("data", []) or []


def normalize(item: dict, category_id=None) -> dict:
    """API 응답에서 필요한 필드만 추출"""
    return {
        "product_id": str(item.get("productId") or ""),
        "name": item.get("productName") or "",
        "price": int(item.get("productPrice") or 0),
        "url": item.get("productUrl") or "",
        "image": item.get("productImage") or "",
        "is_rocket": bool(item.get("isRocket")),
        "free_ship": bool(item.get("isFreeShipping")),
        "rank": item.get("rank") or 999,
        # 골드박스 응답에 원가/할인율이 실려 오는 경우가 있어 방어적으로 수집
        "orig_price": int(item.get("originalPrice")
                          or item.get("basePrice")
                          or item.get("productOriginalPrice") or 0),
        "discount_rate": int(item.get("discountRate")
                             or item.get("productDiscountRate") or 0),
        "category_id": category_id,
        "category_name": item.get("categoryName") or "",
    }
