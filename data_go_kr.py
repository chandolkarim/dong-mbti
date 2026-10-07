"""공공데이터포털 Open API 호출 도우미. 외부 패키지 없이 표준 라이브러리만 씁니다.

API가 달라도 그대로 쓰는 부분을 모았습니다.

- clean_key(): 인증키 앞뒤 공백을 지우고, Encoding 키가 들어오면 한 번 풀어 이중 인코딩을 막습니다.
- build_url(): 요청 주소를 만듭니다. urlencode가 인증키를 한 번 인코딩하므로 Decoding 키를 넣습니다.
- masked(): 로그에 찍을 요청 주소에서 인증키 자리를 ***로 가립니다.
- call_api(): 호출하고 (header, body)를 돌려줍니다. HTTP 오류·시간 초과·XML 오류 응답을 이유와 함께 알립니다.
- parse_body(): 받은 글자(JSON 또는 XML)를 읽고 결과 코드를 확인합니다. 모의 응답 파일에도 씁니다.
- read_items(): body.items.item을 항상 목록으로 돌려줍니다(여러 개 → 배열, 하나 → 객체, 없음 → 빈 값).
"""

import http.client
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


KEY_PARAM_NAMES = {"servicekey"}  # 대소문자와 상관없이 가립니다(serviceKey, ServiceKey)

# 오류 코드별로 확인할 것 (공공데이터포털 FAQ와 제공기관 활용가이드의 오류 코드표 기준)
CODE_HINTS = {
    "01": "제공기관 또는 포털 서버 문제일 수 있습니다. 잠시 뒤 다시 실행하세요.",
    "02": "제공기관 서버 문제일 수 있습니다. 잠시 뒤 다시 실행하세요.",
    "03": "조건에 맞는 데이터가 없다는 뜻입니다(NODATA). 요청 변수 값을 확인하세요.",
    "04": "제공기관 서버 문제일 수 있습니다. 잠시 뒤 다시 실행하세요.",
    "05": "제공기관 서버가 제때 응답하지 않았습니다. 잠시 뒤 다시 실행하세요.",
    "10": "요청 변수 이름과 값을 API 상세 화면의 요청변수 표와 비교하세요.",
    "11": "필수 요청 변수가 빠졌습니다. API 상세 화면의 요청변수 표를 확인하세요.",
    "12": "요청 주소(서비스 주소·오퍼레이션 이름)에 오타가 없는지 확인하세요.",
    "20": "인증키가 요청에 들어갔는지, 활용신청과 승인 상태(마이페이지 → Open API → 활용신청 현황)를 확인하세요.",
    "21": "일시적으로 쓸 수 없는 인증키입니다. 마이페이지에서 활용신청 상태를 확인하세요.",
    "22": "하루 호출 허용량(트래픽)을 넘었습니다. 다음 날 초기화된 뒤 다시 실행하세요.",
    "23": "짧은 시간에 너무 많이 호출했습니다. 잠시 뒤 다시 실행하세요.",
    "30": "등록되지 않은 인증키입니다. Decoding 키를 넣었는지 확인하고, 활용신청 직후라면 잠시 뒤 다시 실행하세요.",
    "31": "인증키 활용 기간이 끝났습니다. 마이페이지에서 활용 기간을 확인하세요.",
    "99": "원인을 알 수 없는 오류입니다. 잠시 뒤 다시 실행하고, 반복되면 교수자에게 로그를 보여 주세요.",
}

# 새 게이트웨이가 글자로만 돌려주는 오류 문장 (게이트웨이 API 활용가이드의 오류 코드표 기준)
TEXT_HINTS = {
    "api token quota exceeded": "하루 호출 허용량을 넘었습니다. 다음 날 초기화된 뒤 다시 실행하세요.",
    "api rate limit exceeded": "동시에 요청이 몰렸습니다. 잠시 뒤 다시 실행하세요.",
    "api not found": "요청 주소에 오타가 없는지, 폐기된 API는 아닌지 확인하세요.",
    "error forwarding request to backend server": "제공기관 서버와 연결하지 못했습니다. 잠시 뒤 다시 실행하세요.",
    "error receiving response from backend server": "제공기관 서버가 응답하지 않았습니다. 잠시 뒤 다시 실행하세요.",
    "unexpected error": "일시적인 시스템 오류입니다. 잠시 뒤 다시 실행하세요.",
    "unauthorized": "인증키가 없거나 유효하지 않습니다. Secrets의 DATA_GO_KR_KEY 값(Decoding 키)을 확인하세요.",
    "forbidden": "이 API의 활용신청 내역이 확인되지 않습니다. 활용신청과 승인 상태를 확인하세요.",
}


class ApiError(Exception):
    """API 호출이나 응답이 약속과 다를 때 씁니다. 메시지에는 인증키를 넣지 않습니다."""


def clean_key(raw):
    """앞뒤 공백·줄바꿈을 지웁니다. %가 들어 있으면 Encoding 키로 보고 한 번 풉니다."""
    key = (raw or "").strip()
    if "%" in key:
        key = unquote(key)
    return key


def build_url(endpoint, params, key, key_param="serviceKey"):
    """요청 주소 = 서비스 주소/오퍼레이션 + ? + 인증키 + 요청 변수."""
    query = {key_param: key}
    query.update(params)
    return endpoint + "?" + urlencode(query)


def masked(url):
    """로그에 찍어도 되도록 인증키 자리를 ***로 바꾼 요청 주소를 돌려줍니다."""
    parts = urlsplit(url)
    pairs = [(name, "***" if name.lower() in KEY_PARAM_NAMES else value)
             for name, value in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(pairs, safe="*")))


def scrub(text, key):
    """메시지에 인증키가 섞여 들어가지 않게 원래 모양과 인코딩된 모양을 모두 가립니다."""
    text = str(text)
    if key:
        for form in sorted({key, quote(key, safe=""), quote_plus(key)}, key=len, reverse=True):
            if form:
                text = text.replace(form, "***")
    return text


def _snippet(text, limit=120):
    one_line = " ".join(str(text).split())
    return one_line[:limit] + ("…" if len(one_line) > limit else "")


def _decode(raw):
    if isinstance(raw, str):
        return raw
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def xml_to_dict(element):
    """XML 요소를 dict로 바꿉니다. 같은 이름의 자식이 여럿이면 목록, 하나면 dict, 내용이 없으면 빈 글자입니다."""
    children = list(element)
    if not children:
        return (element.text or "").strip()
    result = {}
    for child in children:
        value = xml_to_dict(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


def gateway_error(info, label, status=None):
    """OpenAPI_ServiceResponse(포털 공통 오류 응답)를 사람이 읽을 문장으로 바꿉니다."""
    header = info.get("cmmMsgHeader", info) if isinstance(info, dict) else {}
    err = str(header.get("errMsg", "")).strip()
    auth = str(header.get("returnAuthMsg", "")).strip()
    code = str(header.get("returnReasonCode", "")).strip()
    # 예전 형식: errMsg=SERVICE ERROR, returnAuthMsg=오류 이름 / 새 게이트웨이: errMsg=오류 이름, returnAuthMsg=한글 설명
    names = [part for part in (err, auth) if part and part != "SERVICE ERROR"]
    where = f" (HTTP {status})" if status else ""
    hint = CODE_HINTS.get(code.zfill(2) if code.isdigit() else code, "API 상세 화면의 오류 코드표를 확인하세요.")
    return f"{label} 인증·접근 오류{where}: {' · '.join(names) or '이름 없음'} (코드 {code or '없음'}). {hint}"


def parse_body(raw, ok_codes=("00",), label="API", status=None):
    """응답 글자를 읽어 (header, body)를 돌려줍니다. 오류 응답이면 ApiError를 냅니다."""
    where = f" (HTTP {status})" if status else ""
    text = _decode(raw).lstrip()
    if not text:
        raise ApiError(f"{label} 응답이 비어 있습니다{where}.")
    lowered = text[:200].lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        raise ApiError(f"{label}에서 JSON 대신 웹페이지가 왔습니다{where}. 요청 주소가 맞는지 확인하세요: {_snippet(text)}")
    if text.startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError as error:
            raise ApiError(f"{label} 응답을 XML로 읽지 못했습니다{where}: {error} · {_snippet(text)}") from None
        if root.tag == "OpenAPI_ServiceResponse":
            raise ApiError(gateway_error(xml_to_dict(root), label, status))
        data = {root.tag: xml_to_dict(root)}
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            hint = next((h for phrase, h in TEXT_HINTS.items() if phrase in text.lower()), None)
            if hint:
                raise ApiError(f"{label} 오류 응답{where}: {_snippet(text)}. {hint}") from None
            raise ApiError(f"{label} 응답을 JSON으로 읽지 못했습니다{where}: {_snippet(text)}. "
                           "요청 변수에 JSON 지정(예: _type=json)을 넣었는지 확인하세요.") from None
    if not isinstance(data, dict):
        raise ApiError(f"{label} 응답 모양이 예상과 다릅니다{where}: {_snippet(text)}")
    if "OpenAPI_ServiceResponse" in data:
        raise ApiError(gateway_error(data["OpenAPI_ServiceResponse"], label, status))
    root = data.get("response", data)  # 'response'로 감싸지 않은 API도 있습니다
    if not isinstance(root, dict):
        raise ApiError(f"{label} 응답 모양이 예상과 다릅니다{where}: {_snippet(text)}")
    header = root.get("header") or {}
    body = root.get("body") or {}
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise ApiError(f"{label} 응답의 header·body 모양이 예상과 다릅니다{where}: {_snippet(text)}")
    code = str(header.get("resultCode", "")).strip()
    if code not in ok_codes:
        message = str(header.get("resultMsg", "")).strip().rstrip(".") or "메시지 없음"
        hint = CODE_HINTS.get(code, "API 상세 화면의 오류 코드표에서 이 코드를 찾아보세요.")
        raise ApiError(f"{label} 결과 코드가 {code or '(없음)'}입니다{where}: {message}. "
                       f"성공 코드는 {', '.join(ok_codes)}입니다. {hint}")
    return header, body


def read_items(body):
    """body.items.item을 항상 목록으로 바꿉니다. 이 한 곳에서 세 가지 모양을 모두 처리합니다."""
    items = body.get("items")
    if items in (None, "", [], {}):
        return []  # 없음: 빈 글자나 빈 값으로 옵니다
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]  # items가 바로 배열인 API
    if isinstance(items, dict):
        item = items.get("item")
        if item in (None, "", [], {}):
            return []
        if isinstance(item, dict):
            return [item]  # 하나: 배열이 아니라 객체로 옵니다
        if isinstance(item, list):
            return [entry for entry in item if isinstance(entry, dict)]  # 여러 개: 배열
    raise ApiError(f"items 모양을 알 수 없습니다: {type(items).__name__}")


def to_int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _http_error_message(status, body, ok_codes, label):
    text = _decode(body).strip()
    lowered = text.lower()
    known = ("openapi_serviceresponse" in lowered or "resultcode" in lowered
             or any(phrase in lowered for phrase in TEXT_HINTS))
    if known:
        try:
            parse_body(body, ok_codes, label, status=status)
        except ApiError as detail:
            return str(detail)
    return (f"{label}가 HTTP {status} 응답을 돌려주었습니다: {_snippet(text) or '(본문 없음)'}. "
            "잠시 뒤 다시 실행하고, 반복되면 요청 주소와 활용신청 상태를 확인하세요.")


def call_api(endpoint, params, key, *, key_param="serviceKey", ok_codes=("00",), label="API",
             timeout=20, attempts=2, wait=3):
    """API를 호출해 (header, body)를 돌려줍니다. 시간 초과·연결 실패·5xx는 한 번 더 시도합니다."""
    if not key:
        raise ApiError(f"{label}를 부를 인증키가 없습니다.")
    url = build_url(endpoint, params, key, key_param)
    request = Request(url, headers={"User-Agent": "tdrp-open-api/1.0", "Accept": "application/json"})
    problem = ""
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as error:
            try:
                body = error.read()
            except (OSError, http.client.HTTPException):
                body = b""
            if error.code >= 500 and attempt < attempts:
                time.sleep(wait)
                continue
            raise ApiError(scrub(_http_error_message(error.code, body, ok_codes, label), key)) from None
        except (TimeoutError, socket.timeout):
            problem = f"{label}가 {timeout}초 안에 응답하지 않았습니다"
        except URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                problem = f"{label}가 {timeout}초 안에 응답하지 않았습니다"
            else:
                problem = f"{label} 서버에 연결하지 못했습니다: {scrub(error.reason, key)}"
        except (OSError, http.client.HTTPException) as error:
            problem = f"{label} 응답을 받는 중 연결이 끊겼습니다: {scrub(error, key)}"
        else:
            try:
                return parse_body(raw, ok_codes, label)
            except ApiError as detail:
                raise ApiError(scrub(detail, key)) from None
        if attempt < attempts:
            time.sleep(wait)
    raise ApiError(f"{problem} ({attempts}번 시도). 잠시 뒤 다시 실행하세요.")
