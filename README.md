# worker_jarvis

업무 전용 JARVIS. 말하거나 입력하면 JARVIS가 일을 Claude Code에 넘기고, 결과를 소리로 답하며, 산출물은 Obsidian 금고에 남는다.

## 문서 지도

| 문서 | 무엇 |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | 개발 규칙(개발 방법, 브랜치, 작업원장 운용) |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | 설계 스펙 |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | 구현 계획 |
| `prior-work.jsonl` | **작업원장** — 끝난 작업의 결론과 백로그. 조사 전에 먼저 조회한다 (로컬 전용, 공개 저장소에 없음) |
| `brain/` | JARVIS 두뇌 — `CLAUDE.md`(규칙)와 스킬 (로컬 전용, 공개 저장소에 없음 — 없으면 엔진이 돌지 않는다) |

## 실행

```powershell
uv sync
server.bat            # 재시작(떠 있으면 끄고 다시 띄움) — 옵션 없음 = restart
server.bat start      # 시작(이미 떠 있으면 기존 창만 연다)
server.bat stop       # 종료
server.bat status     # 실행 여부
```

- 옵션 앞의 `-` 는 붙여도 된다(`-stop` = `stop`). 직접 실행은 `uv run python -m jarvis.server`.
- 떠 있던 서버를 바꿔 켜는 `restart` 는 새 창을 열지 않는다 — 기존 HUD 창이 다시 연결되며 스스로 새로고침한다.
- HUD 제목줄: `⟳ 새로고침`(화면만), `⏻ 서비스 재시작`(두 번 눌러야 실행, 로그 콘솔은 작업 표시줄에 최소화). 명령 덱: 대화마다 `⧉`, 머리줄에 `⧉ 전체 복사`.

- 처음 실행하면 Obsidian 금고 경로를 묻는다. PC마다 다른 경로는 `config.local.json`에 저장된다(git 제외). 경로를 읽을 수 없게 되면 다시 묻고, HUD 금고 칸에서도 고칠 수 있다.
- Edge 앱 창으로 HUD(`http://127.0.0.1:9300`)가 열린다. **스페이스를 누르고 있는 동안 말하기**, 또는 아래 입력줄에 입력.
- 첫 음성 인식 때 모델(약 1.6GB)을 내려받고 GPU 메모리를 약 1.2GB 쓴다.
- 금고 밖에서 엔진이 **읽기만** 할 곳은 루트의 `read-access.json`(git 제외)에 적는다. 폴더는 그 아래 전부, `*`·`**` 패턴은 맞는 파일만 열린다. 질문마다 다시 읽으므로 고치면 재시작 없이 다음 질문부터 반영된다. 절대경로만 받고, 금고의 금지 규칙(업무 노트 폴더·비밀 이름 파일)이 항상 우선한다.

  ```json
  { "read": ["F:/python_works/**/prior-work.jsonl", "F:/python_works/graphify/docs"] }
  ```

| 환경변수 | 기본값 | 뜻 |
|---|---|---|
| `JARVIS_STT_DEVICE` | `cuda` | `cpu` 로 바꾸면 GPU를 쓰지 않는다 |
| `JARVIS_VOICE` | `M1` | 음성 (`F1`~`F5`, `M1`~`M5`) |
| `JARVIS_MODEL` | Claude Code 기본 | 엔진 모델 |
| `JARVIS_NO_BROWSER` | — | 설정하면 창을 열지 않는다 |

## 구조

| 모듈 | 역할 |
|---|---|
| `jarvis/engine.py` | 엔진 — `claude -p` 를 `brain/` 에서 실행. 금고의 `raw/`·`ai-research/`·`wiki/`·`output/` 만 넘긴다 |
| `jarvis/voice.py` | 귀와 입 — faster-whisper(STT), Supertonic(TTS), 모두 로컬 |
| `jarvis/server.py` · `hud.html` | 얼굴 — 한 화면 HUD, 127.0.0.1 전용 |
| 금고 | 기억 — 산출물은 `output/jarvis/`, 작업 기록은 `wiki/log.md` |

스킬: `vault`(정리·질의·점검·메모), `inbox`(아침 브리핑), `metrics`(`raw/metrics/` 비교), `trends`(`output/jarvis/watchlist.md` 주제), `plan`(오늘의 3가지).

## 테스트

```powershell
uv run pytest -q                       # 단위
$env:JARVIS_LIVE=1; uv run pytest -q tests/test_live.py   # 실호출(claude·음성 모델)
```
