"""동네 MBTI 시험. 모의 응답(fixtures, tests/fixtures)만 쓰고 실제 API는 부르지 않습니다.

프로젝트 폴더에서 실행:  python3 -m unittest discover -s tests -v
"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data_go_kr  # noqa: E402
import main  # noqa: E402
import mbti  # noqa: E402
import stores  # noqa: E402

SAMPLE = ROOT / "fixtures" / "stores_sample.json"
FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, raw):
        self.raw = raw

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def page(items, total, page_no=1, month="202606"):
    return json.dumps({"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE", "stdrYm": month},
                       "body": {"items": items, "totalCount": total, "pageNo": page_no}}).encode()


def item(dong="1123070500", middle="주점", small="호프/맥주", name="가게", branch=""):
    return {"adongCd": dong, "adongNm": "이문1동", "signguNm": "동대문구", "indsLclsNm": "음식",
            "indsMclsNm": middle, "indsSclsNm": small, "bizesNm": name, "brchNm": branch, "lon": "127.05", "lat": "37.6"}


def sheets():
    rules, names = mbti.parse_rules((ROOT / "axis_rules.csv").read_text(encoding="utf-8"))
    types = mbti.parse_types((ROOT / "types.csv").read_text(encoding="utf-8"))
    return rules, names, types


class SheetTests(unittest.TestCase):
    def test_default_sheets_are_valid(self):
        rules, names, types = sheets()
        self.assertEqual(len(types), 16)
        self.assertEqual(names["E"], "밤 문화")
        self.assertIn("@프랜차이즈", rules["TF"]["T"])

    def test_rules_missing_side(self):
        with self.assertRaisesRegex(mbti.RuleError, "EI 축의 I 쪽"):
            mbti.parse_rules("축,쪽,키워드\nEI,E,주점\nSN,S,편의점\nSN,N,카페\nTF,T,@프랜차이즈\nTF,F,@개인가게\nJP,J,헬스\nJP,P,오락\n")

    def test_rules_wrong_side(self):
        with self.assertRaisesRegex(mbti.RuleError, "2행의 쪽은 E 또는 I"):
            mbti.parse_rules("축,쪽,키워드\nEI,S,주점\n")

    def test_rules_missing_column(self):
        with self.assertRaisesRegex(mbti.RuleError, "필수 열이 없습니다: 키워드"):
            mbti.parse_rules("축,쪽\nEI,E\n")

    def test_types_missing_one(self):
        text = (ROOT / "types.csv").read_text(encoding="utf-8").replace("INFP,", "XXXX,")
        with self.assertRaisesRegex(mbti.RuleError, "MBTI 네 글자가 아닙니다"):
            mbti.parse_types(text)


class ComputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = stores.from_fixture(SAMPLE)
        cls.data = mbti.compute(cls.result["stores"], *sheets())
        cls.by_name = {d["name"]: d for d in cls.data["dongs"]}

    def test_fixture_reads_all_items(self):
        self.assertEqual(self.result["received"], 1812)
        self.assertEqual(self.result["base_month"], "202606")

    def test_small_dong_is_held(self):
        small = self.by_name["작은동"]
        self.assertTrue(small["held"])
        self.assertEqual(small["type"], "")

    def test_every_judged_dong_has_type_and_three_similar(self):
        for dong in self.data["dongs"]:
            if dong["held"]:
                continue
            self.assertRegex(dong["type"], r"^[EI][SN][TF][JP]$")
            self.assertEqual(len(dong["similar"]), 3)
            self.assertNotIn(dong["code"], [s["code"] for s in dong["similar"]])
            self.assertTrue(dong["nickname"])

    def test_party_street_is_extravert(self):
        self.assertEqual(self.by_name["이문1동"]["type"][0], "E")
        self.assertEqual(self.by_name["중계1동"]["type"][0], "I")

    def test_neighbours_are_similar(self):
        similar = [s["code"] for s in self.by_name["이문1동"]["similar"]]
        self.assertEqual(similar[0], self.by_name["회기동"]["code"])

    def test_letters_split_about_half(self):
        judged = [d for d in self.data["dongs"] if not d["held"]]
        for index in range(4):
            lefts = sum(1 for d in judged if d["type"][index] == mbti.AXES[index][1])
            self.assertTrue(abs(lefts - len(judged) / 2) <= 1.5, (index, lefts))

    def test_features_are_above_seoul_average(self):
        for dong in self.data["dongs"]:
            for feature in dong["features"]:
                self.assertGreater(feature["times"], 1)

    def test_zero_hit_keywords_are_reported(self):
        lines = main.summary(self.data)
        self.assertTrue(any("맞는 업종이 하나도 없는 키워드" in line for line in lines))

    def test_brand_strips_branch_suffix(self):
        self.assertEqual(mbti._brand("교촌치킨 회기점"), mbti._brand("교촌치킨"))
        self.assertNotEqual(mbti._brand("회기점"), "")

    def test_branch_name_means_chain(self):
        flags = mbti.chain_flags(stores.normalize([item(name="동네빵집"), item(name="GS25", branch="이문점")])[0])
        self.assertEqual(flags, [False, True])

    def test_empty_stores_fail(self):
        with self.assertRaisesRegex(mbti.RuleError, "상가업소가 없습니다"):
            mbti.compute([], *sheets())


class ApiTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("stores.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pages_until_total(self):
        responses = [FakeResponse(page([item(), item()], 3, 1)), FakeResponse(page([item()], 3, 2))]
        with mock.patch("data_go_kr.urlopen", side_effect=responses) as opener:
            result = stores.fetch_all("KEY", log=lambda *_: None)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(result["received"], 3)
        self.assertEqual(result["base_month"], "202606")
        url = opener.call_args_list[1].args[0].full_url
        for part in ("divId=ctprvnCd", "key=11", "pageNo=2", "numOfRows=1000", "type=json", "serviceKey=KEY"):
            self.assertIn(part, url)

    def test_short_read_fails(self):
        responses = [FakeResponse(page([item()], 5, 1)), FakeResponse(page([], 5, 2))]
        with mock.patch("data_go_kr.urlopen", side_effect=responses):
            with self.assertRaisesRegex(data_go_kr.ApiError, "5개 중 1개만"):
                stores.fetch_all("KEY", log=lambda *_: None)

    def test_rows_without_dong_are_skipped(self):
        got, skipped = stores.normalize([item(), item(dong=""), {"adongCd": "1", "indsMclsNm": "", "indsSclsNm": ""}])
        self.assertEqual((len(got), skipped), (1, 2))

    def test_auth_error_is_reported(self):
        with mock.patch("data_go_kr.urlopen", return_value=FakeResponse((FIXTURES / "auth_error.xml").read_bytes())):
            with self.assertRaisesRegex(data_go_kr.ApiError, "코드 30"):
                stores.fetch_all("KEY", log=lambda *_: None)

    def test_preview_hides_key(self):
        self.assertIn("serviceKey=***", stores.request_preview())


class MainTests(unittest.TestCase):
    def run_main(self, args, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}, clear=False), redirect_stdout(out), redirect_stderr(err):
            for name in ("DATA_GO_KR_KEY", "AXIS_CSV_URL", "TYPE_CSV_URL", "GITHUB_ACTIONS"):
                if name not in (env or {}):
                    os.environ.pop(name, None)
            code = main.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_fixture_run_writes_html_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "index.html"
            code, out, _ = self.run_main(["--api-fixture", str(SAMPLE), "--output", str(output)])
            self.assertEqual(code, 0)
            html = output.read_text(encoding="utf-8")
            self.assertIn("이문1동", html)
            self.assertNotIn("/*__DATA__*/", html)
            data = json.loads((Path(tmp) / "dong.json").read_text(encoding="utf-8"))
            self.assertEqual(data["meta"]["dongs"], 10)
            self.assertIn("행정동 10곳", out)

    def test_missing_key_mentions_fixture(self):
        code, _, err = self.run_main(["--output", "/tmp/never.html"])
        self.assertEqual(code, 1)
        self.assertIn("--api-fixture", err)

    def test_missing_key_in_actions(self):
        code, out, _ = self.run_main(["--output", "/tmp/never.html"], {"GITHUB_ACTIONS": "true"})
        self.assertEqual(code, 1)
        self.assertIn("::error::인증키가 없습니다", out)

    def test_cache_skips_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "stores.json"
            stores.save_cache(stores.from_fixture(SAMPLE), cache)
            with mock.patch("data_go_kr.urlopen") as opener:
                code, _, _ = self.run_main(["--cache", str(cache), "--output", str(Path(tmp) / "index.html")])
            self.assertEqual(code, 0)
            opener.assert_not_called()

    def test_api_failure_keeps_previous_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "index.html"
            output.write_text("이전 배포", encoding="utf-8")
            with mock.patch("data_go_kr.urlopen", return_value=FakeResponse((FIXTURES / "result_error.json").read_bytes())):
                code, out, err = self.run_main(["--output", str(output)], {"DATA_GO_KR_KEY": "SECRET-KEY"})
            self.assertEqual(code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "이전 배포")
            self.assertIn("serviceKey=***", out)
            self.assertNotIn("SECRET-KEY", out + err)


if __name__ == "__main__":
    unittest.main()
