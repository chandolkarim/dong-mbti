"""상가업소 목록으로 행정동마다 네 축 점수·MBTI 유형·특징 업종·닮은 동네를 계산합니다. 외부 패키지 불필요.

계산 방법 (페이지에도 그대로 공개합니다)
- 축마다 왼쪽 업종(예: E 밤 문화)과 오른쪽 업종(예: I 조용한 공부)을 시트의 키워드로 고릅니다.
  업종 이름은 '중분류 소분류'를 이어 붙인 글자에서 키워드를 찾습니다.
- 동의 왼쪽 비율 = 왼쪽 업소 / (왼쪽 + 오른쪽 업소). 서울의 모든 동 가운데 이 비율이 몇 번째인지(백분위)를 구해
  절반보다 위면 왼쪽 글자, 아니면 오른쪽 글자를 줍니다. 그래서 서울 전체로 보면 글자가 반반 나옵니다.
- T/F는 키워드 대신 프랜차이즈 여부로 셉니다. 지점명이 있거나 서울에서 같은 상호가 CHAIN_MIN번 이상 나오면 프랜차이즈입니다.
- 특징 업종 = 동의 소분류 비율 ÷ 서울 소분류 비율이 가장 큰 3개(동 안에서 FEATURE_MIN개 이상인 것만).
- 닮은 동네 = 중분류 비율 벡터의 코사인 유사도가 가장 큰 3곳.
- 업소가 MIN_STORES개보다 적은 동은 판정을 보류합니다.
"""

from collections import Counter, defaultdict
import csv
import io
import math
import re


MIN_STORES = 30
CHAIN_MIN = 5
FEATURE_MIN = 3
AXIS_MIN = 3  # 한 축의 양쪽 업소를 합쳐 이보다 적으면 그 축은 서울 가운데(50%)로 둡니다
SIMILAR_COUNT = 3
CHAIN_KEYWORD = "@프랜차이즈"
INDIE_KEYWORD = "@개인가게"

AXES = (
    ("EI", "E", "I"),
    ("SN", "S", "N"),
    ("TF", "T", "F"),
    ("JP", "J", "P"),
)
LETTERS = {letter for _, left, right in AXES for letter in (left, right)}


class RuleError(ValueError):
    """축 규칙 시트나 유형 시트가 약속과 다를 때 씁니다."""


# ── 시트 읽기 ──────────────────────────────────────────────

def _rows(text, required, sheet):
    reader = csv.DictReader(io.StringIO(text))
    header = [(name or "").strip() for name in (reader.fieldnames or [])]
    missing = [name for name in required if name not in header]
    if missing:
        raise RuleError(f"{sheet} 시트에 필수 열이 없습니다: {', '.join(missing)} / 현재 열: {', '.join(header) or '(없음)'}")
    for number, row in enumerate(reader, start=2):
        row = {(key or "").strip(): (value or "").strip() for key, value in row.items() if key is not None}
        if any(row.values()):
            yield number, row


def parse_rules(text):
    """축 규칙 시트: 축(EI·SN·TF·JP), 쪽(그 축의 글자), 키워드, 설명(그 쪽의 이름, 예: 밤 문화)."""
    rules = {axis: {left: [], right: []} for axis, left, right in AXES}
    names = {}
    for number, row in _rows(text, ("축", "쪽", "키워드"), "축 규칙"):
        axis, side, keyword = row["축"].upper(), row["쪽"].upper(), row["키워드"]
        pair = next((entry for entry in AXES if entry[0] == axis), None)
        if not pair:
            raise RuleError(f"축 규칙 {number}행의 축은 EI·SN·TF·JP 중 하나여야 합니다: {row['축']}")
        if side not in pair[1:]:
            raise RuleError(f"축 규칙 {number}행의 쪽은 {pair[1]} 또는 {pair[2]}여야 합니다: {row['쪽']}")
        if not keyword:
            raise RuleError(f"축 규칙 {number}행의 키워드가 비어 있습니다.")
        if keyword.startswith("@") and keyword not in (CHAIN_KEYWORD, INDIE_KEYWORD):
            raise RuleError(f"축 규칙 {number}행: @로 시작하는 키워드는 {CHAIN_KEYWORD}, {INDIE_KEYWORD}만 쓸 수 있습니다.")
        rules[axis][side].append(keyword)
        if row.get("설명") and side not in names:
            names[side] = row["설명"]
    for axis, left, right in AXES:
        for side in (left, right):
            if not rules[axis][side]:
                raise RuleError(f"축 규칙 시트에 {axis} 축의 {side} 쪽 키워드가 하나도 없습니다.")
            names.setdefault(side, side)
    return rules, names


def parse_types(text):
    """유형 시트: 유형(ENFP 등 16개), 별명, 소개."""
    types = {}
    for number, row in _rows(text, ("유형", "별명", "소개"), "유형"):
        code = row["유형"].upper()
        if len(code) != 4 or any(code[i] not in AXES[i][1:] for i in range(4)):
            raise RuleError(f"유형 시트 {number}행의 유형이 MBTI 네 글자가 아닙니다: {row['유형']}")
        types[code] = {"nickname": row["별명"], "intro": row["소개"]}
    missing = [a + b + c + d for a in "EI" for b in "SN" for c in "TF" for d in "JP"
               if a + b + c + d not in types]
    if missing:
        raise RuleError(f"유형 시트에 없는 유형이 있습니다: {', '.join(missing)}")
    return types


# ── 계산 ───────────────────────────────────────────────────

def _category(store):
    return f"{store['middle']} {store['small']}".strip()


def _brand(name):
    """상호에서 괄호와 띄어 쓴 마지막 '○○점'을 지워 같은 가게 이름끼리 묶습니다. 예: '교촌치킨 회기점' → '교촌치킨'."""
    words = re.sub(r"\(.*?\)", " ", name or "").split()
    if len(words) > 1 and words[-1].endswith("점"):
        words = words[:-1]
    return "".join(words).lower()


def chain_flags(stores):
    counts = Counter(_brand(store["name"]) for store in stores if store["name"])
    return [bool(store["branch"]) or (bool(store["name"]) and counts[_brand(store["name"])] >= CHAIN_MIN)
            for store in stores]


def _matches(category, keywords):
    return any(keyword in category for keyword in keywords if not keyword.startswith("@"))


def percentile_ranks(values):
    """값 목록 → 0~1 백분위(같은 값은 평균 순위). 서울에서 이 값보다 작은 동의 비율에 가깝습니다."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        rank = (i + j) / 2 / max(len(values) - 1, 1)
        for k in range(i, j + 1):
            ranks[order[k]] = rank
        i = j + 1
    return ranks


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def compute(stores, rules, names, types):
    """업소 목록 → {"dongs": [...], "meta": {...}}. 화면과 dong.json이 이 결과를 그대로 씁니다."""
    if not stores:
        raise RuleError("계산할 상가업소가 없습니다.")
    chains = chain_flags(stores)
    by_dong = defaultdict(list)
    info = {}
    for store, chain in zip(stores, chains):
        by_dong[store["dong"]].append((store, chain))
        info.setdefault(store["dong"], (store["dong_name"], store["gu"]))

    middles = sorted({store["middle"] for store in stores if store["middle"]})
    seoul_middle = Counter(store["middle"] for store in stores)
    seoul_small = Counter(_category(store) for store in stores)
    total = len(stores)

    keyword_hits = {}
    for axis, left, right in AXES:
        for side in (left, right):
            for keyword in rules[axis][side]:
                if keyword.startswith("@"):
                    hits = sum(chains) if keyword == CHAIN_KEYWORD else total - sum(chains)
                else:
                    hits = sum(1 for store in stores if keyword in _category(store))
                keyword_hits[f"{axis}·{side}·{keyword}"] = hits

    dongs = []
    for code, members in by_dong.items():
        dong_name, gu = info[code]
        n = len(members)
        middle_counts = Counter(store["middle"] for store, _ in members)
        small_counts = Counter(_category(store) for store, _ in members)
        small_names = {_category(store): store["small"] or store["middle"] for store, _ in members}
        shares = {}
        for axis, left, right in AXES:
            if axis == "TF" and (CHAIN_KEYWORD in rules[axis][left] or INDIE_KEYWORD in rules[axis][right]):
                a = sum(1 for _, chain in members if chain)
                b = n - a
            else:
                a = sum(1 for store, _ in members if _matches(_category(store), rules[axis][left]))
                b = sum(1 for store, _ in members if _matches(_category(store), rules[axis][right]))
            shares[axis] = {"left": a, "right": b, "share": a / (a + b) if a + b >= AXIS_MIN else None}
        features = []
        for category, count in small_counts.items():
            if count >= FEATURE_MIN:
                lift = (count / n) / (seoul_small[category] / total)
                features.append({"name": small_names[category], "count": count, "times": round(lift, 1)})
        features.sort(key=lambda entry: (-entry["times"], -entry["count"], entry["name"]))
        dongs.append({
            "code": code, "name": dong_name, "gu": gu, "stores": n, "held": n < MIN_STORES,
            "_shares": shares, "features": [f for f in features if f["times"] > 1][:3],
            "vec": [round(middle_counts[m] / n, 4) for m in middles],
        })

    judged = [d for d in dongs if not d["held"]]
    for axis, left, right in AXES:
        values = [d["_shares"][axis]["share"] for d in judged]
        known = [v for v in values if v is not None]
        middle_value = sorted(known)[len(known) // 2] if known else 0.5
        ranks = percentile_ranks([v if v is not None else middle_value for v in values])
        for dong, rank in zip(judged, ranks):
            side = left if rank >= 0.5 else right
            top = max(1, math.ceil((1 - rank) * 100)) if side == left else max(1, math.ceil(rank * 100))
            entry = dong["_shares"][axis]
            dong.setdefault("axes", []).append({
                "axis": axis, "letter": side, "left": left, "right": right,
                "left_name": names[left], "right_name": names[right],
                "rank": round(rank, 3), "top": top,
                "left_count": entry["left"], "right_count": entry["right"],
            })
    for dong in judged:
        dong["type"] = "".join(a["letter"] for a in dong["axes"])
        dong.update(types[dong["type"]])

    sims_all = []
    for dong in judged:
        scored = []
        for other in judged:
            if other is dong:
                continue
            sim = cosine(dong["vec"], other["vec"])
            scored.append((sim, other["code"]))
            if dong["code"] < other["code"]:
                sims_all.append(sim)
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        dong["similar"] = [{"code": code, "sim": round(sim, 3)} for sim, code in scored[:SIMILAR_COUNT]]
    for dong in dongs:
        dong.pop("_shares")
        if dong["held"]:
            dong.update({"type": "", "axes": [], "similar": []})

    dongs.sort(key=lambda d: (d["gu"], d["name"], d["code"]))
    type_counts = Counter(d["type"] for d in judged)
    return {
        "dongs": dongs,
        "meta": {
            "middles": middles,
            "seoul_vec": [round(seoul_middle[m] / total, 5) for m in middles],
            "cos_min": round(min(sims_all), 4) if sims_all else 0,
            "cos_max": round(max(sims_all), 4) if sims_all else 1,
            "stores": total, "dongs": len(dongs), "judged": len(judged), "held": len(dongs) - len(judged),
            "chain_ratio": round(sum(chains) / total, 3),
            "type_counts": dict(sorted(type_counts.items())),
            "keyword_hits": keyword_hits,
            "rules": {"min_stores": MIN_STORES, "chain_min": CHAIN_MIN, "feature_min": FEATURE_MIN,
                      "similar": SIMILAR_COUNT},
            "names": names,
            "keywords": {axis: rules[axis] for axis, _, _ in AXES},
        },
    }
