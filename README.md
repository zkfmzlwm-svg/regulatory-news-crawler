# regulatory-news-crawler

해외 주요 의약품 규제 뉴스를 수집 → 목록에서 원하는 기사만 체크 → 내가 정한 서식으로 요약해주는 CLI 도구.

## 동작 흐름

1. `crawl` : `config/sources.yaml` 에 등록된 사이트(FDA, EMA, MHRA, Health Canada, TGA, WHO, RAPS 등 기본 제공 + 직접 추가한 사이트)에서 기사를 수집해 로컬 DB(`data/articles.db`)에 저장
2. `list` : 수집된 기사를 번호와 함께 나열
3. `check` : 요약하고 싶은 기사 번호를 체크
4. `summarize` : 체크된 기사만 원문을 가져와 `config/summary_format.yaml` 에 정의한 서식대로 요약 파일(md/txt/docx) 생성

## 설치

```bash
pip install -r requirements.txt
```

## 사용법

```bash
# 1) 기사 수집
python main.py crawl

# 2) 목록 확인 (번호, 체크상태, 날짜, 출처, 제목)
python main.py list
python main.py list --source "FDA - Press Announcements" --keyword biosimilar

# 3) 원하는 기사 체크 (콤마/범위 지원)
python main.py check 1,3,5-8

# 4) 체크된 기사 요약 생성 (config/summary_format.yaml 서식 적용)
python main.py summarize
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

`config/summary_format.yaml` 에서 요약 결과에 어떤 항목이 들어갈지 자유롭게 정의합니다.

```yaml
output_format: "md"   # md | txt | docx
fields:
  - key: "title_kr"
    label: "제목(국문)"
    instruction: "기사 제목을 자연스러운 한국어로 번역"
  - key: "summary"
    label: "핵심 요약"
    instruction: "핵심 내용을 불릿 3~5개로 요약"
  ...
```

`instruction` 은 AI 요약 시 각 항목에 어떤 내용을 채울지 지시하는 문구이므로, 원하는 서식/관점(예: "임상시험 영향 중심으로 정리", "GMP 담당자 관점에서 정리")으로 자유롭게 수정하면 됩니다.

## 번역·분석 품질을 높이려면 (선택)

환경변수 `ANTHROPIC_API_KEY` 를 설정하면 Claude API를 이용해 기사 본문을 실제로 읽고 한국어 번역/분석/시사점까지 채워서 요약합니다.

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python main.py summarize
```

API 키가 없으면 번역·분석 없이 원문 문장을 추출해 채우는 단순 요약으로 자동 대체됩니다(오프라인에서도 동작).

## 참고

- 이 저장소를 실행하는 네트워크 환경에 따라 일부 규제기관 사이트가 접근 제한될 수 있습니다. `crawl` 은 사이트 단위로 실패를 격리하므로 한 사이트가 실패해도 나머지는 정상 수집됩니다.
- 기본 제공 RSS 주소는 각 기관 사이트 개편 시 바뀔 수 있으니, 수집이 안 되면 `config/sources.yaml` 의 URL을 최신 주소로 갱신하세요.
