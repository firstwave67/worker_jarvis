# 업무 노트 연결 (graphify) — 설계

## 목적

JARVIS가 업무 노트(`raw/evernote/`, 비밀 포함)를 쓰되, 밖(Anthropic)으로 나가는 것을 "질문과 관련된 노트 구간"으로 줄이고 비밀 값은 나가지 않게 한다.

## 결정

- `raw/evernote/` 에 대한 엔진의 기본 Read·Edit 금지는 **유지**한다. 업무 노트는 전용 MCP 도구 두 개로만 연다 — 통제 지점을 코드 한 곳(`jarvis/notes.py`)에 둔다.
- MCP 서버 `jarvis-notes`(stdio, `mcp` 2.x `MCPServer`)를 엔진 기동 때 `--mcp-config` 로 붙인다. `--setting-sources project` 에서도 동작한다. 도구 이름은 `mcp__jarvis-notes__notes_search`, `mcp__jarvis-notes__notes_read`.
- 민감 판정 규칙은 graphify(`validation_poc.py`)에서 **복사**한다 — 회사 PC에서 경로가 달라 직접 import 하지 않는다.

## 도구

`notes_search(query, limit=8)` → `{"index_date", "results": [{"path", "lines", "label", "via": "index"|"text", "sensitive"}]}`
- 대상은 `raw/evernote/` 아래 노트만.
- 색인: 금고 `output/graphify/graph.json` 노드의 `label` 과 질의의 2·3글자 조각 겹침 점수(shared_search 와 같은 방식). 노드의 `evidence`(본문 인용)는 돌려주지 않는다.
- 글자 검색: 안전 판정을 통과한 노트의 제목·본문에서 질의 단어가 모두 들어간 첫 줄. 민감 노트는 **제목만** 대조한다 — 본문을 검색하면 "이 값이 들어 있나"를 묻는 질의로 비밀을 추측할 수 있다.
- 민감 노트(현재 내용으로 판정, 또는 색인의 `sensitive_locator`)는 `sensitive: true`, 위치만.
- 색인 결과 다음에 글자 검색 결과, 경로 중복 제거, `limit`(최대 20)으로 자른다.

`notes_read(path, start=1, end=0)` → `{"path", "start", "end", "total_lines", "text", "redacted"}` 또는 `{"error"}`
- `path` 는 금고 기준 상대경로, `raw/evernote/` 아래 `.md` 만. 실제 경로가 그 폴더 밖이면(`..`, 절대경로, 링크) 거부.
- 읽을 때마다 현재 내용으로 민감 판정 → 민감이면 거부하고 위치만 알린다.
- 구간은 최대 200줄. `end=0` 이면 `start` 부터 200줄.
- 비밀처럼 보이는 줄(키·토큰·비밀번호 대입, 키 패턴)은 `[가림]` 으로 바꾸고 개수를 `redacted` 에.
- 줄 앞에 줄 번호를 붙인다(출처 인용용).

## JARVIS 변경

- `jarvis/notes.py`: 판정·가리기·검색·읽기 (순수 함수 + `Notes` 클래스).
- `jarvis/notes_mcp.py`: MCP 서버 진입점. 인자로 금고 경로.
- `jarvis/engine.py`: `--mcp-config`(PYTHONPATH=프로젝트 루트), allow 에 두 도구, 진행 단계 문구("노트 검색: …", "노트 읽는 중: …").
- `brain/.claude/skills/notes/SKILL.md`, `brain/CLAUDE.md` 의 evernote 안내 갱신.

## 검증

- 단위: 경로 탈출 거부, 민감 거부, 줄 가리기, 200줄 자르기, 색인+글자 결과 병합, 민감 노트 본문 미검색, MCP 도구 목록.
- 실호출(가짜 금고): 민감 노트 카나리·일반 노트 속 비밀 줄 카나리가 답에 없고, 일반 구간은 읽힌다. 진행 단계 문구 확인.
- 실제 금고: 개발자 질문 3~5개로 정답 노트 적중 확인(정답은 개발자 제공).

## 범위 밖

색인 자동 갱신(모델 서버 필요), 첨부(이미지·PDF) 읽기, evernote 쓰기.
