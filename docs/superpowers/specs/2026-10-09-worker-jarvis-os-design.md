# worker_jarvis OS — 설계 (v1)

## 1. 목적과 성공 기준

업무 전용 JARVIS. 말하면(또는 입력하면) JARVIS가 일을 Claude Code에 넘기고, 결과를 소리로 답하며, 모든 산출물은 마크다운 금고에 남는다.

| 개발자가 말한 것 | 이 설계의 해석 |
|---|---|
| Speak → Route → Execute → Remember | HUD(음성/텍스트) → 라우터 → `claude -p` + 스킬 → 금고 파일 |
| 모듈 4개, 아무 조각이나 교체 | 엔진·음성·HUD·금고가 함수 하나/폴더 하나 경계로 나뉜다 |
| DB 없음, 마크다운만 | 상태는 파일과 프로세스 메모리뿐 |
| 오디오는 PC 밖으로 안 나감 | STT·TTS 모두 로컬 모델 |
| 한 화면, 탭 없음 | HUD는 HTML 한 장 |

**추정(확인 안 됨):** 개발자의 실제 업무일 표는 받지 못했다. 스킬 다섯 개는 데이터 원천을 고정하지 않고 "금고에 넣어 둔 것 + 연결된 도구(Gmail 커넥터, 웹 검색)"로 동작하게 만든다. 실제 원천이 정해지면 스킬 파일만 고친다.

**성공 기준 (검증 항목)**
1. 텍스트 질문 → Claude Code가 금고 안에서 스킬을 골라 실행 → 답변 + 음성 요약 한 줄이 돌아온다.
2. 스킬 5개(vault, inbox, metrics, trends, plan)가 각자 정해진 경로에 정해진 모양의 마크다운을 남긴다.
6. 금고 경로를 설정으로 바꿀 수 있고, 읽을 수 없는 경로면 다시 묻는다. 엔진은 허용 폴더 4개 밖을 읽지 못한다.
3. 한국어 TTS로 만든 음성을 STT에 넣으면 원문과 거의 같은 글이 나온다(문자 오류율 15% 이하).
4. HUD 한 화면에 시스템 상태·명령 기록·오늘 계획·음성 상태·금고 현황이 보이고 실시간 갱신된다.
5. 다른 웹 페이지가 localhost로 JARVIS를 호출할 수 없다(출처 검사).

## 2. 조사 반영 (GitHub)

뼈대로 통째로 들일 저장소는 **없다** — 큰 것은 macOS 우선이거나 다중 에이전트·Windows 쓰기 불가, 작은 것은 라이선스·클라우드 TTS 문제. 패턴만 가져온다.

| 출처 | 가져오는 것 |
|---|---|
| Karpathy LLM Wiki (gist 442a6bf5…) | raw(불변)·wiki(LLM 소유)·스키마(CLAUDE.md), `wiki/index.md`·`wiki/log.md`, ingest/query/lint 세 동작 |
| Astro-Han/karpathy-llm-wiki (MIT) | 위 패턴을 스킬 하나로 구현하는 구조 (코드 복사 없이 재작성) |
| AgriciDaniel/claude-obsidian (MIT) | lint 점검 항목(끊긴 링크·고아 페이지·낡은 index), 제품 코드와 금고 분리 |
| COG-second-brain (MIT) | 업무형 스킬 이름, daily-brief 개념 |
| ehteshambuildagents/jarvis | 얇은 브리지가 `claude` CLI를 부르고, 도구 권한 관문을 둔다 |
| openclaw-jarvis-ui (ISC) | HUD 패널 구성, SSE 방송 |
| 음성 조사 | STT faster-whisper large-v3-turbo, TTS Supertonic(한국어 지원, CPU ONNX) |

## 3. 구조

```
worker_jarvis/
  jarvis/
    config.py    금고 경로 설정 — config.local.json 읽기·검증·재요청·저장
    engine.py    엔진 — claude -p 호출, 권한 설정 생성, 세션 유지, SAY 줄 분리
    voice.py     귀와 입 — STT(faster-whisper), TTS(Supertonic). 처음 쓸 때 로드
    server.py    HTTP(HUD·SSE·/ask·/listen·/say·/vault), 상태 수집, 실행 진입점
    hud.html     얼굴 — 한 화면
  brain/         JARVIS 두뇌 (git 추적) — claude -p 의 작업 폴더
    CLAUDE.md            JARVIS 스키마
    .claude/skills/<5개>/SKILL.md
  config.local.json      PC별 설정 (git 제외)
  tests/
금고 (설정값, 예: E:\GoogleDrive\ObsidianVault — Google Drive 미러로 집·회사 공유)
```

- **금고는 개발자가 이미 쓰는 Obsidian 금고다**. Karpathy 위키 템플릿 기반이며 자체 규칙이 있다: `raw/`(사람이 넣는 원본, 불변) · `ai-research/`(AI가 찾은 원본, 새 파일만) · `wiki/`(`_master-index.md` → 주제 폴더 `_index.md` → 글, `log.md` 추가만) · `output/`(보고서·결과물). JARVIS는 이 규칙을 **따르고** 새 최상위 폴더를 만들지 않는다.
- 두뇌(설정·스킬)를 금고 밖 `brain/`에 두는 이유: 개발자 금고에 JARVIS 설정 파일을 섞지 않고, 금고 경로가 PC마다 달라도 두뇌는 하나로 유지한다.
- 엔진 교체 = `engine.ask(text) -> Answer` 하나. 음성 교체 = `voice.transcribe(bytes)`·`voice.speak(text)`. 인터페이스 클래스 없음.

## 4. 금고 경로 설정

- `config.local.json` `{"vault": "<경로>"}` — PC마다 다르므로 git 제외. 집 PC 기본값 없음(처음 실행 때 묻는다).
- 시작 시 검사: 폴더가 있고 읽을 수 있으며 `output/jarvis/`를 만들 수 있어야 한다. 실패하면 **터미널에서 경로를 다시 묻고**, 통과할 때까지 반복한 뒤 저장한다.
- 실행 중 금고를 못 읽게 되면(드라이브 미러 끊김 등) HUD 금고 칸이 경고와 함께 경로 입력란을 띄우고, `POST /vault`가 같은 검사 후 저장한다. 그동안 `/ask`는 "금고 경로를 읽을 수 없습니다"로 거절한다.

## 5. 엔진 — `claude -p`

```
claude -p --output-format json --setting-sources project --settings <JSON>
       --add-dir <금고> [--resume <id>] [--model $JARVIS_MODEL]
  (cwd = brain/, 프롬프트는 stdin)
```

- `--setting-sources project`: 사용자 설정(플러그인·훅·출력 스타일)을 끊는다. `--bare`는 OAuth 로그인을 못 써서 제외.
- **읽기 범위 = 금고의 허용 폴더 4개만**(금고 곳곳에 API 키·비밀이 있다): `--add-dir`로 `raw/`·`ai-research/`·`wiki/`·`output/`만 넘기고 금고 맨 위는 넘기지 않는다. 작업 폴더 밖 파일은 `-p`에서 Read·Grep 모두 권한 거부된다(실측 2026-10-09, 가짜 금고 카나리 값으로 확인). 이 네 폴더에 비밀이 없다는 것은 개발자 전제다. 없는 폴더(`raw/captures` 등 하위)는 필요할 때 만들되 허용 폴더 4개 자체가 없으면 만들지 않고 건너뛴다.
- 권한 JSON은 실행 때 만들어 `--settings`로 넘긴다: 기본 모드 `acceptEdits`(편집은 작업 폴더 안만). 허용: WebSearch·Gmail **읽기**(`search_threads`·`get_thread`·`get_message`). Read·Glob·Grep은 허용 목록에 **넣지 않는다** — 경로 없이 넣으면 작업 폴더 밖까지 열린다. 거부: Bash·PowerShell·**WebFetch**(메일 속 지시로 금고 내용을 URL 에 실어 내보내는 프롬프트 주입 차단 — 전체 검토 2026-10-09), brain 편집, 허용 폴더 안의 민감 이름(`*recovery*`·`*2FA*`·`*password*`·`*비밀번호*`·`*.env`) 읽기.
- 거부 규칙 경로 표기는 `//<드라이브 소문자>/<경로>`(예: `//e/GoogleDrive/ObsidianVault/wiki/**/*2FA*`). 실측: `//C:/…`는 차단되지 않아 내용이 샜고 `//c/…`만 실제 거부가 기록됐다.
- 응답 JSON의 `result`·`session_id`·`is_error`를 쓴다. 세션은 서버 프로세스 동안 잇고 날짜가 바뀌면 새로 연다.
- 음성 요약 규약: 마지막 줄 `SAY: <한국어 1~2문장>`. 없으면 본문 첫 문장(실측: brain/CLAUDE.md 규칙만으로 지켜졌다).
- 동시 실행 1건(전역 잠금), 바쁘면 409. 시간 제한 300초.

## 5-1. 기억 — 스킬과 산출 위치

| 스킬 | 입력 | 출력 |
|---|---|---|
| vault | 금고 규칙 그대로: compile(raw·ai-research→wiki, index·log 갱신) / query(`_master-index.md`부터, 출처 인용) / lint(`output/lint-report-날짜.md`, 고치지 않음) / capture(받아 적은 메모) | capture는 `raw/captures/YYYY-MM-DD-HHMM.md` **새 파일만**(사람이 말한 원본이므로 raw) |
| inbox | Gmail 최근 24시간, `raw/captures/` 오늘 것 | `output/jarvis/briefs/날짜.md` — 꼭 답할 것 / 알아둘 것 / 넘겨도 됨 |
| metrics | `raw/metrics/` 최신 vs 직전 | `output/jarvis/metrics/날짜.md` — 지표·현재·이전·변화·메모 표 |
| trends | `output/jarvis/watchlist.md` 주제 + 웹 검색 | `output/jarvis/trends/날짜.md` — 주제별 3건(출처·왜 중요한가) |
| plan | 오늘 브리핑·수치·동향, 직전 계획의 미완료 | `output/jarvis/plans/날짜.md` — 정확히 3건(할 일·왜·완료 기준) |

원천이 비어 있으면 지어내지 않고 "어디에 무엇을 넣으면 되는지"를 답한다. `log.md`에는 금고 형식 `## [YYYY-MM-DD HH:MM] 동작 | 설명 | 파일`로 남긴다.

## 6. 음성

- 누르고 말하기: **HUD 창에서 스페이스를 누르고 있는 동안** 브라우저가 마이크를 녹음(MediaRecorder, webm) → `POST /listen` → 서버 STT. 전역 단축키는 쓰지 않는다 — 다른 앱에 스페이스가 입력되지 않게 막으려면 전역 키 억제가 필요하고, 관리자 권한 문제가 있다. 필요해지면 그때 추가.
- STT: faster-whisper `large-v3-turbo`, `language="ko"`, VAD 필터. 기본 CUDA int8_float16(약 2GB), `JARVIS_STT_DEVICE=cpu`로 전환. webm은 faster-whisper 내장 PyAV가 디코드한다.
- TTS: Supertonic, CPU, 한국어. 결과 wav는 `GET /say/<n>.wav`로 브라우저가 재생. 실패하면 글로만 답한다.
- 오디오는 브라우저 ↔ 127.0.0.1 서버 사이만 오간다. 외부로 나가는 것은 **받아 적은 글**뿐(Claude Code 경유 Anthropic) — 회사 정책 확인 필요(열린 항목).

## 7. 얼굴 — HUD

- `server.py`가 `127.0.0.1:9300`에서 `hud.html`을 주고, `GET /events`(SSE)로 상태를 민다. 표준 라이브러리 `ThreadingHTTPServer`, 프레임워크 없음.
- 한 화면 격자: 시스템(CPU·RAM: psutil, GPU: nvidia-smi) / 음성 상태(대기·듣는 중·생각 중·말하는 중) / 명령 덱(이번 실행의 질문·답 기록) / 오늘 계획(`output/jarvis/plans/오늘.md`) / 금고(경로, raw·ai-research·wiki·output 파일 수, 최근 파일, `wiki/log.md` 끝부분). 하단 입력줄(텍스트 질문).
- 갱신: 시스템·금고는 3초 폴링 후 방송. 금고 감시 라이브러리 없음(파일 수천 개까지 충분, 넘으면 watchfiles).
- 실행: `uv run python -m jarvis.server` → 서버 시작 + Edge `--app` 창.

## 8. 보안

- 127.0.0.1에만 바인드.
- POST는 `Origin`이 `http://127.0.0.1:9300` 또는 `http://localhost:9300`일 때만 받는다. `Host` 헤더도 같은 두 값만 — 다른 웹 페이지가 JARVIS를 시켜 메일을 읽게 하는 것(CSRF·DNS 리바인딩)을 막는다.
- 엔진 권한은 5절. 메일 보내기·삭제 도구는 허용 목록에 없다. 금고 맨 위·기타 폴더는 작업 폴더가 아니라 읽히지 않는다.

## 9. 오류 처리

| 상황 | 동작 |
|---|---|
| claude 실패·`is_error`·시간 초과 | HUD에 오류 표시, 음성 "작업에 실패했습니다" |
| 바쁨 | 409, HUD에 "처리 중" |
| STT 결과 빈 문자열 | "못 알아들었습니다", 엔진 호출 안 함 |
| TTS 실패 | 글만 표시 |
| GPU 없음·nvidia-smi 실패 | GPU 칸 "—" |

## 10. 검증

- 단위(pytest, 실프로세스 없음): 경로 설정 검증·재요청·저장, 권한 경로 표기 변환, SAY 분리, claude 명령 구성·JSON 해석(가짜 subprocess), 세션 날짜 전환, 출처 검사, `/ask` 흐름(가짜 엔진), 금고 집계, SSE 형식.
- 실측(`JARVIS_LIVE=1`): ⓪ 가짜 금고에서 허용 폴더 밖 파일이 Read·Grep 모두 차단된다 ① `claude -p` 실호출이 금고 CLAUDE.md·스킬을 쓰고 SAY 줄을 낸다 ② 스킬 5개 각 1회 실행 후 산출 파일 모양 확인 ③ TTS→STT 왕복 문자 오류율 ④ 서버 기동 후 HTTP·SSE 응답, 헤드리스 Edge 스크린샷으로 화면 확인.
- 사람만 확인 가능: 실제 마이크 입력·스피커 출력·한국어 음성 자연스러움.

## 11. 범위 밖 (v1)

전역 단축키, 깨우기 단어, 일정 연동(캘린더 커넥터 없음 — 오늘 계획으로 대신), Textual TUI, 금고 원격 백업, 여러 사용자.

## 12. 열린 항목

- 프롬프트 주입: 메일 본문의 지시로 엔진이 `raw/`·`wiki/` 파일을 덮어쓸 수 있다(`acceptEdits`, 메모용 `raw/captures` 쓰기 때문에 `raw/` 편집 거부를 못 건다). 외부 반출 경로(WebFetch)는 막았다. 민감 정보 통제 기능에서 함께 다룬다.

- **민감 정보 통제는 다음 기능**: 업무에는 민감 정보가 필요하므로 차단이 아니라 "내부에서는 쓰되 외부로 노출되지 않게" 통제한다. v1의 이름 패턴 차단은 그때까지의 임시 안전장치다. 전제: 엔진이 읽은 내용은 Anthropic으로 나간다 — 따라서 값은 로컬 도구 안에서만 쓰고 모델에는 결과만 넘기는 구조가 필요하다. 작업원장 pending 레코드 참조.

- 집·회사 PC가 같은 날 같은 산출 파일을 동시에 쓰면 Google Drive가 충돌 사본을 만든다. v1은 한 번에 한 PC에서만 JARVIS를 띄운다고 가정.
- 금고의 `CLAUDE.md`가 작업 트리에서 삭제된 상태(git에는 있음). JARVIS는 그 규칙을 `brain/`의 vault 스킬에 옮겨 따르며, 금고 파일은 건드리지 않는다.

- 실제 업무일 표와 데이터 원천(수치의 출처, 동향 주제) — 스킬 내용 보정.
- 받아 적은 업무 내용이 Anthropic으로 가는 것에 대한 회사 정책.
- Supertonic 한국어 자연스러움 — 개발자 청취 후 판단, 대안 faster-qwen3-tts(GPU).
