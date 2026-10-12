# 업무 노트 연결 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** JARVIS가 `raw/evernote/` 업무 노트를 전용 도구(검색·구간 읽기)로만, 비밀을 가린 채 쓰게 한다.
**Architecture:** `jarvis/notes.py`(판정·검색·읽기) + `jarvis/notes_mcp.py`(MCP stdio 서버) → 엔진이 `--mcp-config` 로 연결. 기본 Read 금지는 유지.
**Tech Stack:** Python 3.12, `mcp` 2.3 (`MCPServer`), pytest.
**Spec:** `docs/superpowers/specs/2026-10-09-notes-graphify-design.md`

## Global Constraints
- 노트 대상은 `raw/evernote/**.md` 만. 색인은 `<금고>/output/graphify/graph.json`.
- 색인 `evidence` 는 도구 결과에 넣지 않는다. 민감 노트 본문은 검색·읽기 모두 하지 않는다.
- 읽기 최대 200줄, 검색 최대 20건(기본 8).

## Review Focus
- 경로에 공백·한글·이모지가 있어도 읽기·검색이 된다 → Task 1 테스트(이모지 파일명).
- `graph.json` 이 없거나 깨져도 글자 검색만으로 동작한다 → Task 1 테스트.
- `start` 가 파일 길이보다 크거나 음수 → 빈 구간/1로 보정 → Task 1 테스트.
- 비밀 줄 가리기가 일반 문장의 "password reset 안내" 같은 줄까지 지우지 않는다(대입 꼴만) → Task 1 테스트.
- 민감 노트 제목에 질의어가 있으면 위치만 나오고 본문 줄 번호는 없다 → Task 1 테스트.

### Task 1: `jarvis/notes.py` + `tests/test_notes.py`
- Produces: `is_sensitive(rel: str, text: str) -> bool`, `redact(lines: list[str]) -> tuple[list[str], int]`, `class Notes(vault: Path)` with `search(query: str, limit: int = 8) -> dict`, `read(path: str, start: int = 1, end: int = 0) -> dict`.
- [ ] 실패 테스트 → 구현 → 통과 → 커밋.

### Task 2: `jarvis/notes_mcp.py`, `jarvis/engine.py`
- Produces: MCP 서버 `jarvis-notes`(도구 `notes_search`, `notes_read`), `build_cmd` 의 `--mcp-config`, allow 두 도구, `step_label` 문구.
- [ ] 테스트: build_cmd 에 mcp-config(서버 명령·PYTHONPATH·금고 인자), allow 포함; step_label; MCP 서버가 두 도구를 내놓는다(`list_tools`). → 구현 → 커밋.

### Task 3: 두뇌
- [ ] `brain/.claude/skills/notes/SKILL.md`, `brain/CLAUDE.md` evernote 안내 갱신. 커밋.

### Task 4: 실측
- [ ] 가짜 금고 실호출(카나리 2종 미유출, 일반 구간 읽힘, 단계 문구). 실제 금고: 개발자 질문으로 적중 확인. 작업원장, develop 병합.
