# Changelog

버전 규칙: 0.1 → 0.2 → 0.3 … 순으로 올리며, 공식 사용 전까지는 0.x 로 운영한다.

## v0.3 — 2026-10-06

- FiercePharma: 광고 기사(`/sponsored/`) 자동 제외
  - 소스 설정에 `exclude_link_pattern` 옵션 추가 (주소에 해당 문자열이 든 기사는 수집 제외, 모든 type 공통)
- 원문 본문을 못 가져올 때(Cloudflare 차단 등) RSS 의 제목 + 소개글로 요약 생성
  - 이전에는 소개글만 쓰거나 "(본문을 가져오지 못했습니다)" 로 표시됐음
  - 번역 요약 모드에서는 제목도 한국어로 번역됨

## v0.2 — 2026-10-02

- 무료 번역 요약에서 "Server Error (TooManyRequests)" 로 한국어 번역이 안 되던 문제 수정
  - 원인: 기존 deep-translator 가 쓰는 translate.google.com/m 을 Google 이 봇으로 차단
  - 자체 번역 모듈(`src/translate.py`)로 교체: clients5.google.com → translate.googleapis.com → MyMemory 순으로 자동 전환
  - 기사당 1회 요청으로 문장 일괄 번역, 일시 오류(429/5xx)는 재시도
  - `deep-translator` 의존성 제거

## v0.1 — 2026-10-02

- 버전 관리 시작 (`src/__init__.py` 의 `__version__`)
- 수집 대상 기간: 수집 실행 시점 기준 최근 21일(3주) 이내 발행 자료만 수집
  - 발행일을 알 수 없는 기사는 제외하지 않음
  - 사이트별 결과에 기간 외로 제외된 건수 표시
  - CLI: `crawl --days N` 으로 기간 변경 가능 (0 = 제한 없음)
- 기존 기능: 12개 규제기관/업계 사이트 수집, 기사 선택 후 요약(단순/무료 번역), Windows EXE 자동 빌드
