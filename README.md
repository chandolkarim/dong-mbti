# 동네 MBTI

**서울 행정동의 상가 데이터로 동네 성격(MBTI)을 판정하고, 닮은 동네와 동네 궁합을 보여 주는 웹서비스**입니다.
GitHub Actions가 저장소 Secrets의 인증키로 소상공인시장진흥공단_상가(상권)정보 API를 불러 서울 전체 상가업소를 받고,
구글 시트의 축 규칙·유형 문구로 계산해 GitHub Pages에 배포합니다. Python 표준 라이브러리만 씁니다.

```text
상가정보 API(서울 전체, 1,000개씩 페이지 넘김) + 구글 시트 2개(축 규칙, 유형 문구)
  → Actions(push / 버튼 / 매달 2일 08:17): 시험 → API 호출 → 행정동별 업종 비율 → 네 축 백분위 → 유형·특징·닮은 동네
  → index.html + dong.json → GitHub Pages (중간에 멈추면 이전 배포 유지)
  → 브라우저: 동 검색, 성격 카드, 닮은 동네, 동네 궁합 (계산은 dong.json으로만 하므로 인증키가 드러나지 않음)
```

## 파일 역할

| 파일 | 역할 |
|---|---|
| `main.py` | 시트 읽기, 상가정보 읽기(모의 응답 / 저장 파일 / API), 계산, HTML·`dong.json` 생성 |
| `stores.py` | 상가정보 API 설정(요청 주소·인증키 변수·성공 코드·요청 변수·응답 항목)과 페이지 넘기기 |
| `mbti.py` | 네 축 점수, 유형, 특징 업종, 닮은 동네 계산. 산식은 파일 맨 위 설명과 페이지 하단에 공개 |
| `data_go_kr.py` | 공공데이터포털 공통 처리 (수업 예제 그대로) |
| `page_template.html` | 화면. 검색·카드·궁합을 브라우저에서 그립니다 |
| `axis_rules.csv`, `types.csv` | 구글 시트가 없을 때 쓰는 기본 축 규칙과 16유형 문구 |
| `fixtures/stores_sample.json` | 키 없이 확인하는 모의 응답 (가상의 업소 1,812개, 10개 동) |
| `tests/` | 시험 26개: 시트 검사, 계산, 페이지 넘기기, 오류, 키 가림, 실패 시 이전 결과 유지 |

## 1. 키 없이 로컬에서 실행

```bash
python3 main.py --api-fixture fixtures/stores_sample.json --output _site/index.html
python3 -m unittest discover -s tests -v
```

`_site/index.html`을 브라우저로 열면 이문1동 카드가 먼저 보입니다. 로그 끝에 **맞는 업종이 하나도 없는 키워드**가 나오면 축 규칙의 키워드가 실제 업종 이름과 다르다는 뜻입니다.

## 2. 실제 키로 실행 (내 컴퓨터)

1. 공공데이터포털에서 `소상공인시장진흥공단_상가(상권)정보`를 **활용신청**합니다 (개발계정 자동승인, 하루 10,000건).
2. `.env`의 `DATA_GO_KR_KEY=` 뒤에 **Decoding 키**를 붙여 넣습니다. `.env`는 `.gitignore`로 올라가지 않습니다.
3. 실행합니다. `--cache`를 붙이면 받은 업소를 저장해 두고, 다음부터는 API를 다시 부르지 않고 규칙만 바꿔 볼 수 있습니다.

```bash
set -a; source .env; set +a; python3 main.py --cache cache/stores.json --output _site/index.html
```

서울 전체는 약 수십만 개라 1,000개씩 수백 번 호출합니다. 몇 분 걸립니다.

## 3. 저장소 설정

1. Settings → Pages → Source: **GitHub Actions**
2. Settings → Secrets and variables → Actions → **Secrets**: `DATA_GO_KR_KEY` = Decoding 키
3. (선택) 구글 시트 2개를 CSV로 웹에 게시하고 **Variables**에 `AXIS_CSV_URL`, `TYPE_CSV_URL` 등록. 없으면 저장소의 CSV를 씁니다.

시트 열 이름:

- 축 규칙: `축`(EI·SN·TF·JP), `쪽`(그 축의 글자), `키워드`(업종 '중분류 소분류' 글자에서 찾을 말), `설명`(그 쪽의 이름). T/F는 `@프랜차이즈`, `@개인가게`로 씁니다.
- 유형: `유형`(16개 모두), `별명`, `소개`

## 4. 확인할 것

| 상황 | 해 보는 방법 | 기대 결과 |
|---|---|---|
| 정상 | Run workflow | build 로그에 `serviceKey=***`, 업소·동·보류 수, 많은 유형 |
| 키 없음 | Secrets 등록 전 실행 | build 실패, 등록 방법 안내, 이전 배포 유지 |
| API 오류 | 시험 `test_api_failure_keeps_previous_html` | 이유 출력, HTML을 덮어쓰지 않음 |
| 표본이 작은 동 | 모의 응답의 `작은동` | "판정 보류"와 이유 |
| 휴대전화 폭 | 휴대전화로 공개 주소 열기 | 가로 넘침 없음 |

## 출처

- 상가업소: 소상공인시장진흥공단_상가(상권)정보, 공공데이터포털(https://www.data.go.kr/data/15012005/openapi.do), 이용허락범위 제한 없음
- 축 규칙·유형 문구: 이 프로젝트에서 새로 작성. 재미로 보는 판정이며 동네를 평가하려는 것이 아닙니다.
- `fixtures/stores_sample.json`은 실제 응답 구조를 본떠 만든 가상의 데이터입니다.
