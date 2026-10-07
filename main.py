"""상가정보 Open API와 구글 시트(축 규칙·유형 문구)로 '동네 MBTI' 웹페이지를 만듭니다. 외부 패키지 불필요.

상가업소 읽는 순서: --api-fixture 파일 → --cache 파일(있으면) → 환경 변수 DATA_GO_KR_KEY로 API 호출
시트 읽는 순서: 환경 변수 AXIS_CSV_URL·TYPE_CSV_URL → 같은 폴더의 axis_rules.csv·types.csv
잘못된 데이터나 API 오류가 있으면 HTML을 만들기 전에 멈춥니다. Actions에서는 build가 실패하고 이전 배포가 유지됩니다.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import data_go_kr
import mbti
import stores


KST = ZoneInfo("Asia/Seoul")
HERE = Path(__file__).parent
TEMPLATE = HERE / "page_template.html"
KEY_ENV = "DATA_GO_KR_KEY"
SHEETS = {"axis": ("AXIS_CSV_URL", HERE / "axis_rules.csv"), "type": ("TYPE_CSV_URL", HERE / "types.csv")}
DATA_FILE_NAME = "dong.json"
FIXTURE_EXAMPLE = "fixtures/stores_sample.json"


class DataError(ValueError):
    """시트 데이터나 실행 설정이 약속과 다를 때 사용합니다."""


def in_actions():
    return os.environ.get("GITHUB_ACTIONS") == "true"


def read_source(source):
    """URL이면 내려받고, 파일 경로면 읽어서 CSV 문자열을 돌려줍니다."""
    if re.match(r"https?://", str(source)):
        request = Request(str(source), headers={"User-Agent": "dong-mbti/1.0"})
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
        except HTTPError as error:
            raise DataError(f"시트 주소에서 {error.code} 응답을 받았습니다. 웹에 게시했는지, 주소가 맞는지 확인하세요.") from error
        except URLError as error:
            raise DataError(f"시트 주소에 연결하지 못했습니다: {error.reason}") from error
    else:
        raw = Path(source).read_bytes()
    text = raw.decode("utf-8-sig")
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        raise DataError("CSV 대신 웹페이지가 왔습니다. 파일 → 공유 → 웹에 게시에서 CSV로 게시한 주소인지 확인하세요.")
    return text


def load_sheets():
    texts, labels = {}, {}
    for name, (env, fallback) in SHEETS.items():
        url = os.environ.get(env, "").strip()
        texts[name] = read_source(url or fallback)
        labels[name] = "구글 시트" if url else f"로컬 파일 {fallback.name}"
    rules, names = mbti.parse_rules(texts["axis"])
    types = mbti.parse_types(texts["type"])
    print(f"축 규칙: {labels['axis']} · 유형 문구: {labels['type']}")
    return rules, names, types


def missing_key_message():
    if in_actions():
        return (f"인증키가 없습니다. 저장소 Settings → Secrets and variables → Actions → Secrets 탭에서 "
                f"{KEY_ENV}(Decoding 키)를 등록하고 다시 실행하세요. 이번 실행은 배포하지 않으므로 이전 배포가 그대로 유지됩니다.")
    return (f"환경 변수 {KEY_ENV}가 없습니다. 키 없이 확인하려면 모의 응답 파일로 실행하세요: "
            f"python3 main.py --api-fixture {FIXTURE_EXAMPLE} --output _site/index.html")


def load_stores(api_fixture, cache):
    """(결과, 데이터 출처 문구)를 돌려줍니다."""
    if api_fixture:
        return stores.from_fixture(api_fixture), f"모의 응답 파일 {Path(api_fixture).name}"
    if cache and Path(cache).exists():
        print(f"상가정보: 저장해 둔 파일 {cache}을 읽습니다 (API를 다시 부르지 않음)")
        return stores.load_cache(cache), f"저장해 둔 응답 {Path(cache).name}"
    key = data_go_kr.clean_key(os.environ.get(KEY_ENV, ""))
    if not key:
        raise DataError(missing_key_message())
    print(f"상가정보 요청: {stores.request_preview()}")
    result = stores.fetch_all(key)
    if cache:
        stores.save_cache(result, cache)
        print(f"상가정보: {cache}에 저장했습니다")
    return result, f"공공데이터 Open API({stores.SOURCE['name']})"


def build_data(result, origin, rules, names, types, now):
    data = mbti.compute(result["stores"], rules, names, types)
    data["meta"].update({
        "generated_at": now.isoformat(timespec="seconds"),
        "base_month": result.get("base_month", ""),
        "origin": origin,
        "source": stores.SOURCE,
        "received": result["received"], "skipped": result["skipped"],
    })
    return data


def generate_html(data, environment=None):
    environment = os.environ if environment is None else environment
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    commit = environment.get("GITHUB_SHA", "")[:7]
    run = environment.get("GITHUB_RUN_NUMBER", "")
    build = f"실행 #{run} · 커밋 {commit}" if run and commit else "로컬 실행"
    return (TEMPLATE.read_text(encoding="utf-8")
            .replace("/*__DATA__*/null", payload)
            .replace("__BUILD__", build))


def write_atomic(path, text):
    """다 쓴 뒤에 바꿔 넣어, 중간에 멈춰도 반쯤 쓴 파일이 남지 않게 합니다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as tmp:
        tmp.write(text)
    Path(tmp.name).replace(path)


def summary(data):
    meta = data["meta"]
    top = sorted(meta["type_counts"].items(), key=lambda pair: -pair[1])[:3]
    zero = [name for name, hits in meta["keyword_hits"].items() if hits == 0]
    lines = [
        f"동네 MBTI: 업소 {meta['stores']:,}개 · 행정동 {meta['dongs']}곳 (판정 {meta['judged']} · 보류 {meta['held']})"
        f" · 프랜차이즈 비율 {meta['chain_ratio']:.0%}",
        "많은 유형: " + (", ".join(f"{code} {count}곳" for code, count in top) or "없음"),
    ]
    if zero:
        lines.append(f"맞는 업종이 하나도 없는 키워드 {len(zero)}개 (축 규칙 시트에서 업종 이름을 확인하세요): " + ", ".join(zero))
    return lines


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="상가정보 Open API와 구글 시트로 동네 MBTI 웹페이지를 만듭니다.")
    parser.add_argument("--output", default="_site/index.html", type=Path, help="출력 HTML 경로 (같은 폴더에 dong.json도 저장)")
    parser.add_argument("--api-fixture", help="API 대신 읽을 모의 응답 파일 (키 없이 로컬에서 확인할 때)")
    parser.add_argument("--cache", help="받은 업소 목록을 저장·재사용할 파일. 있으면 API를 부르지 않습니다")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    now = datetime.now(KST)
    try:
        rules, names, types = load_sheets()
        result, origin = load_stores(args.api_fixture, args.cache)
        print(f"상가정보: {origin} · 받은 업소 {result['received']:,}개 (건너뜀 {result['skipped']})"
              + (f" · 기준 {result['base_month'][:4]}년 {result['base_month'][4:]}월" if result.get("base_month") else ""))
        data = build_data(result, origin, rules, names, types, now)
    except (DataError, mbti.RuleError, data_go_kr.ApiError, OSError, json.JSONDecodeError) as error:
        print(f"생성 실패: {error}", file=sys.stderr)
        if in_actions():
            print(f"::error::{error}")
            step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if step_summary:
                with open(step_summary, "a", encoding="utf-8") as out:
                    out.write(f"### 동네 MBTI 생성 실패\n\n{error}\n")
        return 1
    for line in summary(data):
        print(line)
    write_atomic(args.output, generate_html(data))
    data_path = args.output.parent / DATA_FILE_NAME
    write_atomic(data_path, json.dumps(data, ensure_ascii=False, indent=1))
    print(f"HTML 생성 완료: {args.output}")
    print(f"JSON 저장 완료: {data_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
