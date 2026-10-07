"""소상공인시장진흥공단_상가(상권)정보 · 행정동 단위 상가업소 조회(storeListInDong)로 서울 전체 상가업소를 받습니다.

수업 예제의 special_days.py를 본떠 ①–⑤를 이 API 상세 화면의 값으로 바꿨습니다.
공통 처리(요청 주소 만들기, 오류 판정, item 정규화)는 data_go_kr.py가 맡습니다.
서울(시도 코드 11)을 한 페이지 1,000개씩 끝까지 넘기며 받습니다. 서울 전체는 수백 번 호출합니다.
"""

import json
from pathlib import Path
import time

import data_go_kr
from data_go_kr import ApiError


LABEL = "상가정보 API"

# ① 요청 주소 = 서비스 URL + 오퍼레이션 (API 상세 화면의 '요청주소')
ENDPOINT = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInDong"

# ② 인증키 변수 이름
KEY_PARAM = "serviceKey"

# ③ 성공 코드
OK_CODES = ("00",)

SOURCE = {
    "name": "소상공인시장진흥공단_상가(상권)정보",
    "operation": "행정동 단위 상가업소 조회(storeListInDong)",
    "portal": "공공데이터포털",
    "url": "https://www.data.go.kr/data/15012005/openapi.do",
    "license": "이용허락범위 제한 없음",
}

SEOUL = "11"
PAGE_SIZE = 1000  # 이 API의 한 페이지 최대 개수
MAX_PAGES = 2000  # 응답이 이상해 끝없이 넘기는 일을 막는 안전장치


def request_params(page, area=SEOUL, div="ctprvnCd", rows=PAGE_SIZE):
    """④ 요청 변수: divId(시도 ctprvnCd·시군구 signguCd·행정동 adongCd), key(그 코드), 페이지, JSON 지정(type=json)."""
    return {"divId": div, "key": area, "numOfRows": rows, "pageNo": page, "type": "json"}


def request_preview(page=1, area=SEOUL):
    """로그에 찍을 요청 주소. 인증키 자리는 ***입니다."""
    return data_go_kr.masked(data_go_kr.build_url(ENDPOINT, request_params(page, area), "***", KEY_PARAM))


def normalize(raw_items):
    """⑤ 응답 항목 → 계산에 쓸 항목. 행정동 코드·업종 분류가 없는 업소는 셀 수 없으므로 건너뜁니다."""
    stores, skipped = [], 0
    for item in raw_items:
        dong = str(item.get("adongCd", "")).strip()
        middle = str(item.get("indsMclsNm", "")).strip()
        small = str(item.get("indsSclsNm", "")).strip()
        if not dong or not (middle or small):
            skipped += 1
            continue
        stores.append({
            "dong": dong,
            "dong_name": str(item.get("adongNm", "")).strip(),
            "gu": str(item.get("signguNm", "")).strip(),
            "large": str(item.get("indsLclsNm", "")).strip(),
            "middle": middle,
            "small": small,
            "name": str(item.get("bizesNm", "")).strip(),
            "branch": str(item.get("brchNm", "")).strip(),
            "lon": _to_float(item.get("lon")),
            "lat": _to_float(item.get("lat")),
        })
    return stores, skipped


def _to_float(value):
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _header_month(header):
    month = str(header.get("stdrYm", "")).strip()
    return month if len(month) == 6 and month.isdigit() else ""


def fetch_all(key, area=SEOUL, timeout=30, pause=0.1, log=print):
    """인증키로 마지막 페이지까지 받아 {stores, total_count, received, skipped, pages, base_month}를 돌려줍니다."""
    stores, received, skipped, total, base_month = [], 0, 0, None, ""
    for page in range(1, MAX_PAGES + 1):
        header, body = data_go_kr.call_api(ENDPOINT, request_params(page, area), key, key_param=KEY_PARAM,
                                           ok_codes=OK_CODES, label=LABEL, timeout=timeout)
        raw_items = data_go_kr.read_items(body)
        base_month = base_month or _header_month(header)
        if total is None:
            total = data_go_kr.to_int(body.get("totalCount"), 0)
            log(f"상가정보: 전체 {total:,}개 · 페이지당 {PAGE_SIZE}개 · 약 {-(-total // PAGE_SIZE)}번 호출")
        got, bad = normalize(raw_items)
        stores += got
        skipped += bad
        received += len(raw_items)
        if page % 50 == 0:
            log(f"  {page}페이지 · {received:,}/{total:,}개")
        if not raw_items or received >= total:
            break
        time.sleep(pause)
    else:
        raise ApiError(f"{LABEL} 페이지가 {MAX_PAGES}쪽을 넘었습니다. totalCount({total})와 응답을 확인하세요.")
    if total and received < total:
        raise ApiError(f"{LABEL}에서 {total:,}개 중 {received:,}개만 받았습니다. 잠시 뒤 다시 실행하세요.")
    return {"stores": stores, "total_count": total or received, "received": received,
            "skipped": skipped, "pages": page, "base_month": base_month}


def from_fixture(path):
    """저장해 둔 응답 파일(모의 응답)을 API 응답처럼 읽습니다. 파일 하나 = 응답 한 페이지입니다."""
    header, body = data_go_kr.parse_body(Path(path).read_bytes(), OK_CODES, LABEL)
    raw_items = data_go_kr.read_items(body)
    stores, skipped = normalize(raw_items)
    return {"stores": stores, "total_count": data_go_kr.to_int(body.get("totalCount"), len(raw_items)),
            "received": len(raw_items), "skipped": skipped, "pages": 1, "base_month": _header_month(header)}


def save_cache(result, path):
    """받은 업소 목록을 저장해 두면 계산 규칙만 바꿀 때 API를 다시 부르지 않아도 됩니다(--cache)."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def load_cache(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
