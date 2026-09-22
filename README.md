# regulatory-news-crawler

해외 주요 의약품 규제 뉴스를 수집 → 화면에서 원하는 기사만 체크 → 내가 정한 서식으로 요약해주는 도구.
화면(웹 GUI)과 터미널(CLI) 두 가지 방식 모두 지원하며, 둘 다 같은 데이터(`data/articles.db`)를 공유합니다.

## 동작 흐름

1. 기사 수집 : `config/sources.yaml` 에 등록된 사이트(FDA, EMA, MHRA, Health Canada, TGA, WHO, RAPS 등 기본 제공 + 직접 추가한 사이트)에서 기사를 수집해 로컬 DB(`data/articles.db`)에 저장
2. 목록에서 원하는 기사 체크
3. 체크한 기사만 원문을 가져와 `config/summary_format.yaml` 에 정의한 서식대로 요약 파일(md/txt/docx) 생성

## 설치

```bash
pip install -r requirements.txt
```

## 화면(GUI)으로 사용하기 — 추천

```bash
streamlit run app.py
```

브라우저가 자동으로 열립니다(수동으로 열려면 http://localhost:8501).

- 왼쪽 사이드바: "🔄 기사 수집 실행" 버튼, 키워드/출처 필터, 사이트 추가
- 사이드바 "4. 내 API 키" — 서버에 `ANTHROPIC_API_KEY`가 설정되어 있지 않아도, 본인 Anthropic API 키를 여기 직접 입력하면 AI 요약을 쓸 수 있습니다. 이 키는 **브라우저 세션에만 저장**되고 파일/서버에 저장되지 않으며, 그 키로 요청한 토큰은 그 키 소유자(본인) 계정으로 과금됩니다. 새로고침하면 다시 입력해야 합니다.
- 본문 표: 기사별 **체크박스**로 원하는 기사 선택
- 요약 버튼 두 가지:
  - **🆓 단순 요약 (토큰 미사용)** — 번역 없이 원문 문장을 그대로 추출. 내용을 먼저 훑어볼 때 사용 (무료)
  - **🤖 AI 요약 생성 (토큰 사용)** — Claude API로 한국어 번역/요약. 서버 환경변수 `ANTHROPIC_API_KEY` 또는 사이드바에 입력한 개인 키가 있어야 활성화됨 (기사당 토큰 소모)
- 요약 결과가 화면에 바로 표시되고, 파일(md/txt/docx)로 다운로드 가능

## 터미널(CLI)로 사용하기

```bash
# 1) 기사 수집
python main.py crawl

# 2) 목록 확인 (번호, 체크상태, 날짜, 출처, 제목)
python main.py list
python main.py list --source "FDA - Press Announcements" --keyword biosimilar

# 3) 원하는 기사 체크 (콤마/범위 지원)
python main.py check 1,3,5-8

# 4) 체크된 기사 요약 생성 (config/summary_format.yaml 서식 적용)
python main.py summarize                 # auto: API 키 있으면 AI, 없으면 단순 요약
python main.py summarize --mode simple    # 토큰 미사용, 번역 없는 단순 요약
python main.py summarize --mode ai        # Claude 요약 강제 (토큰 사용, 키 없으면 에러)
# 특정 파일로 저장
python main.py summarize --output output/my_summary.md
```

요약이 끝나면 대상 기사의 체크는 자동 해제되고 `요약됨` 표시가 붙습니다(`--keep-checked` 로 유지 가능).

## 내가 지정하는 사이트 추가하기

RSS가 있는 사이트:

```bash
python main.py add-source --name "My Site" --url "https://example.com/rss.xml" --type rss
```

RSS가 없는 사이트는 목록 페이지의 CSS 선택자를 지정해 스크래핑합니다:

```bash
python main.py add-source --name "My Site" --url "https://example.com/news" --type html \
  --item-selector "div.news-item" \
  --title-selector "h2.title a" \
  --date-selector "span.date"
```

`config/sources.yaml` 을 직접 열어 수정해도 됩니다.

## 요약 서식 커스터마이징

`config/summary_format.yaml` 의 `style_example` 이 곧 요약 결과의 서식입니다. 기본값은 아래 형태로 되어 있습니다.

```
1. [FDA] Form 483 Observation 의 답변 Guideline 초안 발표
- 기업들이 Form 483 답변서가 데이터 누락, 구조적 결함, 근본 원인 분석 미흡 등 '부적절'한 경우가 많았음
- 핵심 권장사항
  : 구조적이고 리스크 기반(Risk-based) 접근 방식을 취해야 함
  : 단순 수정이 아닌 근본 원인(Root Cause) 을 파악하고 최종 사용자에게 미칠 영향을 평가해야 함
  : 적절한 CAPA 를 구현해야 함
- 제출 Format, 중간 보고서 작성, 과학적/기술적 이견이 있을 때의 처리 방법 등도 언급
- 5월 8일까지 초안에 대한 의견 수렴 중
- 링크: [Responding to FDA Form 483 Observations at the Conclusion of a Drug CGMP Inspection | FDA](https://www.fda.gov/...)
```

- 번호(`1.`), `[태그]`, 제목(원문 영문 그대로), 마지막 `- 링크:` 줄은 프로그램이 자동으로 채웁니다(태그는 `config/sources.yaml` 의 `tag` 값).
- 본문 불릿(`- ` / 하위 항목 `  : `)만 AI가 `style_example` 과 `instruction` 을 참고해 한국어로 번역/요약해 채웁니다.
- 원하는 형식으로 바꾸고 싶으면 `style_example` 을 원하는 예시로 교체하고 `instruction` 을 그에 맞게 수정하면 됩니다.
- `output_format: md | txt | docx` 로 저장 형식을 바꿀 수 있습니다.

## 번역·분석 품질을 높이려면 (선택)

환경변수 `ANTHROPIC_API_KEY` 를 설정하면 Claude API를 이용해 기사 본문을 실제로 읽고 한국어 번역/분석/시사점까지 채워서 요약합니다.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python main.py summarize
```

API 키가 없으면 번역·분석 없이 원문 문장을 추출해 채우는 단순 요약으로 자동 대체됩니다(오프라인에서도 동작).

## 다른 사람(비개발자)과 공유하기 — Streamlit Community Cloud 배포

코드 설치 없이 링크만으로 다른 사람이 웹 화면을 쓰게 하려면 **Streamlit Community Cloud**(무료)에 배포하면 됩니다.

1. 이 저장소를 GitHub에 푸시 (이미 되어 있다면 생략)
2. https://share.streamlit.io 접속 → GitHub 계정으로 로그인
3. "New app" 클릭 → 이 저장소 선택 → Branch: `claude/brave-bell-dce54l`, Main file path: `app.py`
4. 배포 전 **"Advanced settings" → "Secrets"** 에 아래 내용을 붙여넣기 (형식은 `.streamlit/secrets.toml.example` 참고)
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   APP_PASSWORD = "팀에서 공유할 비밀번호"
   ```
   - `APP_PASSWORD` 를 설정하면 접속 시 비밀번호를 물어봅니다. **이 저장소는 공개(public)** 이므로, 링크가 알려지면 누구나 AI 요약(토큰 사용) 버튼을 눌러 내 API 비용이 나갈 수 있습니다 — 팀 내부 공유용이라면 꼭 설정하세요.
5. "Deploy" 클릭 → 몇 분 뒤 `https://xxxx.streamlit.app` 형태의 공개 링크 생성됨
6. 이 링크 + `APP_PASSWORD` 를 사용할 사람들에게 전달

**참고할 점**
- 클라우드 인스턴스는 일정 시간 비활성 시 잠들거나 재시작될 수 있어, 그때마다 수집된 기사(`data/articles.db`)가 초기화될 수 있습니다 — 재시작 후 "🔄 기사 수집 실행"을 다시 눌러주면 됩니다.
- `config/sources.yaml` 의 "사이트 추가"로 저장한 내용도 재배포/재시작 시 초기화될 수 있으니, 자주 쓰는 사이트는 저장소의 `config/sources.yaml` 에 직접 커밋해두는 것을 권장합니다.
- 비개발자에게 설치 없이 쓰게 하는 게 목적이 아니라면(예: 기술팀 내부 공유), 그냥 GitHub 저장소를 공유하고 각자 `pip install -r requirements.txt` 후 `streamlit run app.py` 로 로컬 실행하게 하는 편이 더 간단하고 API 키도 각자 관리할 수 있습니다.

## 참고

- 이 저장소를 실행하는 네트워크 환경에 따라 일부 규제기관 사이트가 접근 제한될 수 있습니다. `crawl` 은 사이트 단위로 실패를 격리하므로 한 사이트가 실패해도 나머지는 정상 수집됩니다.
- 기본 제공 RSS 주소는 각 기관 사이트 개편 시 바뀔 수 있으니, 수집이 안 되면 `config/sources.yaml` 의 URL을 최신 주소로 갱신하세요.
