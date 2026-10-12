# worker_jarvis OS v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 말하거나 입력하면 Claude Code가 금고 허용 폴더 안에서 스킬을 실행하고, 결과를 HUD 한 화면과 한국어 음성으로 돌려준다.

**Architecture:** 표준 라이브러리 HTTP 서버 하나(`jarvis/server.py`)가 HUD·SSE·질문·음성을 처리한다. 엔진은 `claude -p` 하위 프로세스(cwd=`brain/`, 금고 허용 폴더 4개를 `--add-dir`). 음성은 브라우저 녹음 → 서버 faster-whisper → Supertonic wav.

**Tech Stack:** Python 3.12 + uv, faster-whisper 1.2.1(av<16 고정), supertonic 1.3.1, psutil, pytest. 프런트는 HTML 한 장(라이브러리 없음).

**Spec:** `docs/superpowers/specs/2026-10-09-worker-jarvis-os-design.md`

## Global Constraints

- 서버는 `127.0.0.1:9300`에만 바인드. POST는 `Origin`·`Host`가 `127.0.0.1:9300`·`localhost:9300`일 때만.
- 엔진 명령: `claude -p --output-format json --setting-sources project --settings <JSON> --add-dir <허용 폴더들> [--resume id] [--model $JARVIS_MODEL]`, cwd=`brain/`, 프롬프트는 stdin, 시간 제한 300초.
- 허용 폴더: `raw`, `ai-research`, `wiki`, `output` (존재하는 것만). 금고 맨 위는 넘기지 않는다.
- 권한 allow에 Read·Glob·Grep을 넣지 않는다. 거부 경로 표기는 `//<드라이브 소문자>/<경로>`.
- 설정 파일 `config.local.json` `{"vault": "..."}` (git 제외).
- 음성 요약 줄: `SAY: `로 시작하는 마지막 줄.
- subprocess 출력은 `encoding="utf-8"`로 읽는다.

## Review Focus

- 금고 경로에 공백·한글·백슬래시가 있을 때 권한 경로 변환과 `--add-dir`가 깨지지 않아야 한다 → Task 2 테스트.
- `claude`가 JSON이 아닌 출력(로그인 만료 메시지 등)을 내면 서버가 죽지 않고 오류 답을 낸다 → Task 2 테스트.
- 답에 `SAY:` 줄이 없거나 여러 개면 마지막 것, 없으면 첫 문장 → Task 2 테스트.
- 금고가 실행 중 사라지면 `/ask`는 409가 아니라 금고 오류를 낸다 → Task 4 테스트.
- 빈 녹음(스페이스 짧게 누름) → 엔진 호출 없이 "못 알아들었습니다" → Task 4 테스트.

---

### Task 1: 금고 경로 설정 (`jarvis/config.py`)

**Files:** Create `jarvis/__init__.py`, `jarvis/config.py`, `tests/test_config.py`. Modify `.gitignore`(vault/ 줄 제거, `config.local.json` 추가).

**Interfaces — Produces:**
- `ALLOWED = ("raw", "ai-research", "wiki", "output")`
- `check_vault(path: str) -> str | None` — 문제 없으면 None, 있으면 한국어 이유. 조건: 폴더 존재, 읽기 가능, `output/jarvis` 생성 가능.
- `load(cfg_file: Path = CONFIG) -> dict` / `save(cfg: dict, cfg_file=CONFIG)`
- `ensure_vault(ask=input, out=print, cfg_file=CONFIG) -> Path` — 저장된 경로가 통과할 때까지 묻고 저장.

- [ ] 테스트: `check_vault(없는 경로)`는 이유 문자열, `check_vault(tmp_path)`는 None이고 `tmp_path/output/jarvis` 생성됨; `ensure_vault`가 잘못된 저장값 → 묻기 2회(첫 입력 잘못, 둘째 tmp_path) 후 `config.local.json`에 둘째 값 저장.
- [ ] 실패 확인 → 구현 → 통과 → 커밋.

### Task 2: 엔진 (`jarvis/engine.py`)

**Interfaces — Consumes:** `config.ALLOWED`. **Produces:**
- `perm_path(p: Path) -> str` — `E:\A B\금고` → `//e/A B/금고`
- `settings_json(vault: Path) -> str`
- `build_cmd(vault: Path, session: str | None, model: str | None) -> list[str]`
- `split_say(text: str) -> tuple[str, str]` — (본문, 말할 문장)
- `@dataclass Answer(text: str, say: str, ok: bool, session: str | None)`
- `class Engine(vault: Path, run=subprocess.run, today=date.today)` with `ask(prompt: str) -> Answer` — 세션 유지, 날짜 바뀌면 세션 리셋.

- [ ] 테스트: `perm_path(Path(r"E:\GoogleDrive\Obsidian Vault"))=="//e/GoogleDrive/Obsidian Vault"`; settings JSON의 allow에 "Read" 없음·deny에 "Bash" 있음; `build_cmd`가 존재하는 허용 폴더만 `--add-dir`로 넣음; `split_say("본문\nSAY: 요약")==("본문","요약")`, SAY 없으면 첫 문장; 가짜 run이 JSON 반환 → 두 번째 호출에 `--resume <id>`; 날짜 바뀌면 `--resume` 없음; 가짜 run이 비JSON 출력 → `Answer.ok False`.
- [ ] 실패 확인 → 구현 → 통과 → 커밋.

### Task 3: 두뇌 (`brain/CLAUDE.md`, `brain/.claude/skills/*/SKILL.md`)

- [ ] `brain/CLAUDE.md`: JARVIS 역할, 금고 허용 폴더와 규칙(raw 불변·captures 새 파일만, ai-research 새 파일만, wiki 금고 규칙, output/jarvis 산출, 지난 날짜 파일 무단 수정 금지), 메일 보내기 금지, 한국어, 마지막 줄 `SAY:`.
- [ ] 스킬 5개: vault(compile/query/lint/capture — 금고 스키마 요약), inbox, metrics, trends, plan — 스펙 5-1 표의 입력·출력 경로와 칸을 그대로.
- [ ] 검증은 Task 6 실측. 커밋.

### Task 4: 음성 + 서버 (`jarvis/voice.py`, `jarvis/server.py`)

**Interfaces — Produces:**
- `voice.transcribe(audio: bytes) -> str`, `voice.speak(text: str) -> bytes`(wav). 모델은 처음 호출 때 로드, `JARVIS_STT_DEVICE`(기본 cuda), 음성 `JARVIS_VOICE`(기본 M1).
- `server.make_server(engine, vault, port=9300, stt=voice.transcribe, tts=voice.speak) -> ThreadingHTTPServer`
- 라우트: `GET /`(hud.html), `GET /events`(SSE), `GET /state`(JSON 스냅숏), `POST /ask {text}`, `POST /listen`(audio bytes), `GET /say/<n>.wav`, `POST /vault {path}`.
- 이벤트 형식: `data: {"type": "...", ...}\n\n`, type ∈ state·heard·answer·error·audio.

- [ ] 테스트(가짜 엔진·stt·tts, 실제 포트 0): Origin 없음/다름 → 403; `/ask` → 200 후 answer 이벤트, say wav 가져오기 200; 동시 두 번째 `/ask` → 409; 금고 폴더 삭제 후 `/ask` → 503 + 금고 오류; `/listen` 빈 STT → 엔진 미호출 + "못 알아들었습니다"; `/state`에 vitals·vault 개수·plan 키.
- [ ] 실패 확인 → 구현 → 통과 → 커밋.

### Task 5: HUD (`jarvis/hud.html`)

- [ ] 한 화면 격자 5칸 + 입력줄. SSE 수신, 스페이스 누르는 동안 MediaRecorder(입력줄 포커스 중엔 무시), 놓으면 `/listen` POST, audio 이벤트 오면 재생. 금고 오류 시 경로 입력란 → `/vault`.
- [ ] 진입점 `python -m jarvis.server`: `ensure_vault` → 서버 → Edge `--app` 창(`JARVIS_NO_BROWSER=1`이면 생략).
- [ ] 검증은 Task 6 화면 확인. 커밋.

### Task 6: 실측 검증 (`tests/test_live.py`, `JARVIS_LIVE=1`)

- [ ] ⓪ 가짜 금고: 허용 폴더 밖 카나리 Read·Grep 거부 ① 엔진 실호출 SAY 줄 ③ TTS→STT 왕복 CER ≤ 0.15.
- [ ] 스킬 5개를 실제 금고에서 1회씩(텍스트 `/ask`) — 산출 파일 경로·모양 확인.
- [ ] 서버 기동, 헤드리스 Edge 스크린샷으로 HUD 확인.
- [ ] README 실행법 갱신, 작업원장 completed 기록, 커밋.
