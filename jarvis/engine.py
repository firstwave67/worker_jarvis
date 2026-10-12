"""엔진 — Claude Code(`claude -p`)를 brain/ 에서 부르고 금고 허용 폴더만 넘긴다."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from jarvis.config import ALLOWED, READ_ACCESS, ROOT, read_access

BRAIN = ROOT / "brain"
TIMEOUT = 300
CRASH_LOG = ROOT / "logs" / "engine-crash.log"
_BUN_CRASH = re.compile(r"Bun has crashed|panic\(|Segmentation fault")


def _log_crash(text: str) -> None:
    """원인 조사를 위해 충돌 원문을 로컬에만 남긴다(화면·대화 기록에는 넣지 않는다)."""
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as f:
            f.write(f"===== {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
    except OSError:
        pass
NOTES_TOOLS = ["mcp__jarvis-notes__notes_search", "mcp__jarvis-notes__notes_read",
               "mcp__jarvis-notes__notes_list"]
GMAIL_READ = ["mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
              "mcp__claude_ai_Gmail__get_message"]
SENSITIVE = ["*recovery*", "*2FA*", "*password*", "*비밀번호*", "*.env"]


@dataclass
class Answer:
    text: str
    say: str
    ok: bool
    session: str | None = None


def perm_path(p: Path) -> str:
    """권한 규칙용 절대경로. Windows에서는 //<드라이브 소문자>/... 만 실제로 막힌다."""
    s = str(p).replace("\\", "/")
    if re.match(r"^[A-Za-z]:", s):
        s = "//" + s[0].lower() + s[2:]
    return s


def read_rules(entries: list[str]) -> list[str]:
    """읽기 전용 허용 규칙. 폴더는 그 아래 전부, 그 밖(파일·패턴)은 적힌 그대로."""
    return [f"Read({perm_path(Path(e))}/**)" if Path(e).is_dir() else f"Read({perm_path(Path(e))})" for e in entries]


def settings_json(vault: Path, extra: list[str] = ()) -> str:
    # Read·Glob·Grep 은 allow 에 넣지 않는다 — 경로 없이 넣으면 작업 폴더 밖까지 열린다.
    # WebFetch 금지 — 메일 속 지시(프롬프트 주입)로 금고 내용을 URL 에 실어 내보낼 수 있다.
    deny = ["Bash", "PowerShell", "WebFetch", f"Edit({perm_path(BRAIN)}/**)"]
    deny += [f"Read({perm_path(vault)}/**/{pat})" for pat in SENSITIVE]
    # 업무 노트(raw/evernote)에는 API 키·비밀이 섞여 있다 — graphify 연결 전까지 읽기·쓰기 모두 막는다
    deny += [f"{tool}({perm_path(vault)}/raw/evernote/**)" for tool in ("Read", "Edit")]
    # 대화 기록은 서버만 쓴다 — 엔진은 읽기만(정제용)
    deny.append(f"Edit({perm_path(vault)}/raw/jarvis-log/**)")
    return json.dumps({"permissions": {"defaultMode": "acceptEdits",
                                       "allow": ["WebSearch", *GMAIL_READ, *NOTES_TOOLS, *read_rules(extra)],
                                       "deny": deny}}, ensure_ascii=False)


def build_cmd(vault: Path, session: str | None, model: str | None) -> list[str]:
    # stream-json: 도구를 쓸 때마다 한 줄씩 나온다 — HUD 진행 단계 표시에 쓴다
    # read-access.json 은 질문마다 다시 읽는다 — 고치면 재시작 없이 다음 질문부터 반영된다
    extra = read_access(READ_ACCESS)
    cmd = [shutil.which("claude") or "claude", "-p", "--output-format", "stream-json", "--verbose",
           "--setting-sources", "project", "--settings", settings_json(vault, extra)]
    if extra:  # 작업 폴더 밖이라 엔진이 스스로 찾지 못한다 — 위치를 알려 준다
        cmd += ["--append-system-prompt", "금고 밖에서 추가로 읽을 수 있는 곳(읽기 전용, Read 도구로): " + ", ".join(extra)]
    for name in ALLOWED:
        if (vault / name).is_dir():
            cmd += ["--add-dir", str(vault / name)]
    # 업무 노트는 이 도구로만 연다 — 기본 Read 는 raw/evernote 에서 계속 막혀 있다
    notes = {"command": sys.executable, "args": [str(ROOT / "jarvis" / "notes_mcp.py"), str(vault)],
             "env": {"PYTHONPATH": str(ROOT)}}
    cmd += ["--mcp-config", json.dumps({"mcpServers": {"jarvis-notes": notes}}, ensure_ascii=False)]
    if session:
        cmd += ["--resume", session]
    if model:
        cmd += ["--model", model]
    return cmd


def split_say(text: str) -> tuple[str, str]:
    """(본문, 소리 내어 읽을 문장). 마지막 SAY: 줄, 없으면 본문 첫 문장."""
    lines = text.strip().splitlines()
    says = [l for l in lines if l.startswith("SAY:")]
    body = "\n".join(l for l in lines if not l.startswith("SAY:")).strip()
    if says:
        return body, says[-1][4:].strip()
    plain = " ".join(l.lstrip("#-* ").strip() for l in lines if l.strip() and not l.startswith("#"))
    m = re.match(r".+?[.!?。](?=\s|$)", plain)
    return body, (m.group(0) if m else plain[:120])


def step_label(name: str, inp: dict, vault: Path) -> str | None:
    """도구 사용 한 건 → HUD 에 보일 한 줄. 사람에게 의미 없는 내부 도구는 None."""
    def rel(p):
        try:
            return Path(p).relative_to(vault).as_posix()
        except ValueError:
            return Path(p).name
    if name == "Read":
        return f"읽는 중: {rel(inp.get('file_path', ''))}"
    if name in ("Write", "Edit", "MultiEdit"):
        return f"쓰는 중: {rel(inp.get('file_path', ''))}"
    if name in ("Grep", "Glob"):
        return f"찾는 중: {inp.get('pattern', '')}"
    if name == "WebSearch":
        return f"웹 검색: {inp.get('query', '')}"
    if name == "mcp__jarvis-notes__notes_search":
        return f"노트 검색: {inp.get('query', '')}"
    if name == "mcp__jarvis-notes__notes_list":
        return f"노트 목록: {inp.get('folder', '')}"
    if name == "mcp__jarvis-notes__notes_read":
        return f"노트 읽는 중: {inp.get('path', '')}"
    if name.startswith("mcp__claude_ai_Gmail__"):
        return "메일 확인 중"
    if name == "Skill":
        return f"스킬: {inp.get('skill', '')}"
    return None


def run_stream(cmd, *, input, cwd, timeout, on_line):
    """claude 출력을 줄 단위로 넘긴다. 시간 초과 때 자식(node·MCP)까지 죽인다 — 안 그러면 파이프가 막혀 안 돌아온다."""
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    with subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, encoding="utf-8", errors="replace", creationflags=flags) as p:
        expired = threading.Event()

        def kill():
            expired.set()
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.kill()
        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            p.stdin.write(input)
            p.stdin.close()
            for line in p.stdout:
                on_line(line.rstrip("\n"))
            err = p.stderr.read()
            p.wait()
        finally:
            timer.cancel()
    if expired.is_set():
        raise subprocess.TimeoutExpired(cmd, timeout)
    return subprocess.CompletedProcess(cmd, p.returncode, None, err)


class Engine:
    def __init__(self, vault: Path, run=run_stream, today=date.today):
        self.vault, self.run, self.today = vault, run, today
        self.session, self.day = None, today()

    def ask(self, prompt: str, on_step=None) -> Answer:
        if self.today() != self.day:  # 날짜가 바뀌면 새 대화
            self.session, self.day = None, self.today()
        cmd = build_cmd(self.vault, self.session, os.environ.get("JARVIS_MODEL"))
        for attempt in (1, 2):
            a, crashed = self._ask_once(cmd, prompt, on_step)
            if not crashed:
                return a
            # claude(Bun) 가 시작 직후, 아무 일도 하기 전에 죽었다 — 부작용이 없으니 한 번만 다시 띄운다
        return Answer("엔진(Claude Code)이 시작하다 멈췄습니다. 잠시 뒤 다시 시도해 주세요.",
                      "엔진이 시작하다 멈췄습니다. 다시 말씀해 주세요.", False)

    def _ask_once(self, cmd: list[str], prompt: str, on_step) -> tuple[Answer, bool]:
        """(답, 시작 직후 충돌 여부). 충돌은 이벤트를 하나도 내기 전에 Bun 이 죽은 경우만."""
        found, stray, events = {}, [], []

        def on_line(line):
            try:
                ev = json.loads(line)
            except ValueError:
                ev = None
            if not isinstance(ev, dict):
                if line.strip():
                    stray.append(line)
                return
            events.append(ev.get("type"))
            if ev.get("type") == "result":
                found.update(ev)
            elif ev.get("type") == "assistant" and on_step:
                for c in (ev.get("message") or {}).get("content") or []:
                    if c.get("type") == "tool_use" and (label := step_label(c.get("name", ""), c.get("input") or {}, self.vault)):
                        on_step(label)
        p = None
        try:
            # 모델은 현재 시각을 모른다 — 로그·메모 파일명에 쓸 시각을 붙여 준다
            stamped = f"[지금: {datetime.now():%Y-%m-%d %H:%M}]\n{prompt}"
            p = self.run(cmd, input=stamped, cwd=BRAIN, timeout=TIMEOUT, on_line=on_line)
            if not found:
                raise ValueError("no result")
            d = found
        except subprocess.TimeoutExpired:
            self.session = None
            return Answer("시간 초과(300초)로 작업을 멈췄습니다.", "작업 시간이 초과됐습니다.", False), False
        except (ValueError, OSError) as e:
            out = ("\n".join(stray) + (f"\n{p.stderr}" if p and p.stderr else "")).strip()
            self.session = None
            if not events and _BUN_CRASH.search(out):
                _log_crash(out)
                return Answer("", "", False), True
            return Answer(out or str(e), "작업에 실패했습니다.", False), False
        if d.get("is_error"):  # 만료된 세션을 계속 이어 붙이지 않게 버린다
            self.session = None
            return Answer(str(d.get("result", "")), "작업에 실패했습니다.", False), False
        self.session = d.get("session_id") or self.session
        body, say = split_say(str(d.get("result", "")))
        return Answer(body, say, True, self.session), False
