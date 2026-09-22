# regulatory-news-crawler

해외 주요 의약품 규제 뉴스를 수집 → 화면에서 원하는 기사만 체크 → 한국어 요약으로 정리해주는 **데스크톱 프로그램**.
API 키나 별도 서버 없이, `python app.py` 하나로 바로 창이 뜨는 개별 프로그램 형태로 동작합니다.

## 동작 흐름

1. 기사 수집 : `config/sources.yaml` 에 등록된 사이트(FDA, EMA, MHRA, Health Canada, TGA, WHO, RAPS 등 기본 제공 + 직접 추가한 사이트)에서 기사를 수집해 로컬 DB(`data/articles.db`)에 저장
2. 목록 표에서 원하는 기사의 '선택' 칸을 클릭해 체크
3. 체크한 기사만 원문을 가져와 요약 생성 (md/txt/docx 파일로 저장 + 화면에도 표시)

## 설치

```bash
pip install -r requirements.txt
```

Windows의 python.org 배포판에는 tkinter(GUI 라이브러리)가 기본 포함되어 있어 추가 설치가 필요 없습니다.
(Linux에서 `ModuleNotFoundError: No module named 'tkinter'` 가 나오면 `sudo apt install python3-tk` 로 설치)

## 실행하기

```bash
python app.py
```

바로 프로그램 창이 뜹니다. 브라우저나 서버가 필요 없습니다.

- 상단 툴바: "🔄 기사 수집" 버튼, 키워드 검색, "요약 안 한 것만" 필터, "사이트 관리"
- 표의 **'선택' 칸을 클릭**해 요약할 기사를 고르고(다시 클릭하면 해제), **제목을 더블클릭**하면 원문이 브라우저로 열립니다
- 요약 버튼 두 가지 (**둘 다 API 키나 비용이 전혀 들지 않습니다**):
  - **🆓 단순 요약 (영어)** — 번역 없이 원문 문장을 그대로 추출
  - **🌍 번역 요약 (무료)** — Google 번역(비공식)으로 문장을 한국어로 번역
- 요약 결과가 창 안에 바로 표시되고, "📁 다른 이름으로 저장"으로 원하는 위치에 파일(md/txt/docx)로 저장 가능
- "사이트 관리" 버튼으로 등록된 사이트 확인 및 RSS 사이트 추가

## 터미널(CLI)로 사용하기

화면 없이 스크립트/자동화로 쓰고 싶을 때는 `main.py` 를 씁니다.

```bash
# 1) 기사 수집
python main.py crawl

# 2) 목록 확인 (번호, 체크상태, 날짜, 출처, 제목)
python main.py list
python main.py list --source "FDA - Press Announcements" --keyword biosimilar

# 3) 원하는 기사 체크 (콤마/범위 지원)
python main.py check 1,3,5-8

# 4) 체크된 기사 요약 생성
python main.py summarize                  # 기본값: 무료 번역(한국어)
python main.py summarize --mode simple    # 번역 없이 원문 문장만 추출
# 특정 파일로 저장
python main.py summarize --output output/my_summary.md
```

요약이 끝나면 대상 기사의 체크는 자동 해제되고 `요약됨` 표시가 붙습니다(`--keep-checked` 로 유지 가능).

`app.py`(화면)와 `main.py`(터미널)는 같은 데이터(`data/articles.db`)를 공유하므로 섞어서 써도 됩니다.

## 내가 지정하는 사이트 추가하기

화면에서: "사이트 관리" 버튼 → 이름/태그/RSS URL 입력 후 "추가".

터미널에서, RSS가 있는 사이트:

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

## 요약 서식

각 기사는 아래 형태로 정리됩니다.

```
1. [FDA] FDA Approves New Generic Drug Pathway Rule
- (번역되었거나 원문 그대로인) 문장 불릿...
- 링크: [원문 제목 | 태그](URL)
```

- 번호, `[태그]`(출처), 제목(원문 영문 그대로), 마지막 `- 링크:` 줄은 프로그램이 자동으로 채웁니다.
- 본문 불릿은 선택한 모드(단순 추출 / 무료 번역)에 따라 채워집니다. AI 요약 같은 문장 재구성·분석은 하지 않고, 추출한 문장을 그대로(또는 번역해서) 나열합니다.
- `config/summary_format.yaml` 의 `output_format: md | txt | docx` 로 저장 형식을 바꿀 수 있습니다.

## 다른 사람과 공유하기

이 프로그램은 서버 없이 각자의 컴퓨터에서 실행하는 방식입니다. 다른 사람에게 전달하려면:

1. 이 저장소를 공유(또는 GitHub 링크 전달)
2. 상대방이 `pip install -r requirements.txt` 후 `python app.py` 실행

exe 파일 하나로 만들어 Python 설치 없이 실행하게 하려면 [PyInstaller](https://pyinstaller.org/) 를 씁니다.

```bash
pip install pyinstaller
pyinstaller --onefile --windowed app.py
```

`dist/app.exe` (Windows) 가 생성되며, 더블클릭만으로 실행됩니다. 단, `config/`, `data/`, `output/` 폴더는 exe와 같은 위치에 함께 있어야 합니다.

## 참고

- 이 프로그램은 API 키나 결제 등록이 전혀 필요 없습니다. "🌍 번역 요약"은 Google 번역의 비공식 무료 엔드포인트를 사용하므로, 가끔 번역이 실패할 수 있습니다(그 경우 해당 문장만 원문으로 표시되고 나머지는 정상 처리됩니다).
- 실행 환경의 네트워크에 따라 일부 규제기관 사이트가 접근 제한될 수 있습니다. `crawl` 은 사이트 단위로 실패를 격리하므로 한 사이트가 막혀도 나머지는 정상 수집됩니다.
- 기본 제공 RSS 주소는 각 기관 사이트 개편 시 바뀔 수 있으니, 수집이 안 되면 `config/sources.yaml` 의 URL을 최신 주소로 갱신하세요.
