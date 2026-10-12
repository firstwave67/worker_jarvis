"""HUD 서버 — 127.0.0.1 에서 HUD·SSE·질문·음성을 처리한다. 실행: python -m jarvis.server"""
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from jarvis import config, voice
from jarvis.config import ALLOWED
from jarvis.engine import Engine
from jarvis.notes import redact

PORT = 9300
HUD = Path(__file__).with_name("hud.html")
TICK = 3
ACKS = ("네, 확인해 볼게요.", "잠시만요, 찾아볼게요.", "알겠습니다, 바로 볼게요.")
MAX_BODY = 20 * 1024 * 1024  # 녹음 한 번 상한


def gpu() -> str | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3).stdout
        u, used, total = [x.strip() for x in out.splitlines()[0].split(",")]
        return f"{u}% · {int(used) / 1024:.1f}/{int(total) / 1024:.0f}GB"
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def split_sentences(text: str) -> list[str]:
    """문장 끝(. ! ? 。) 뒤 공백에서 나눈다. 6자 미만 조각("네.")은 앞 문장에 붙인다 — 너무 잘게 끊으면 어색하다."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?。])\s+", text.strip()):
        if not part:
            continue
        if out and len(part) < 6:
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def safe_host(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name) or "pc"


def append_log(vault: Path, question: str, answer: str, ok: bool, via: str) -> Path:
    """대화 한 건을 금고 raw/jarvis-log/날짜-PC.md 에 덧붙인다 — 기억 정제의 원본.
    PC 이름을 파일명에 넣어 집·회사 PC 가 같은 파일을 동시에 쓰는 Google Drive 충돌을 피한다."""
    d = vault / "raw" / "jarvis-log"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{date.today()}-{safe_host(socket.gethostname())}.md"
    head = "" if f.exists() else f"# JARVIS 대화 기록 {date.today()} ({socket.gethostname()})\n\n"
    q, _ = redact(question.splitlines())
    a, _ = redact(answer.splitlines())
    entry = (f"## {datetime.now():%H:%M} · {via}{'' if ok else ' · 실패'}\n\n"
             f"**질문:** {' '.join(q)}\n\n**답:**\n\n" + "\n".join(a) + "\n\n")
    with f.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(head + entry)
    return f


def vault_state(vault: Path) -> dict:
    if not vault.is_dir():
        return {"path": str(vault), "ok": False}
    counts, files = {}, []
    for name in ALLOWED:
        found = [p for p in (vault / name).rglob("*.md")] if (vault / name).is_dir() else []
        counts[name] = len(found)
        files += found
    def mtime(p):  # Google Drive 미러가 임시 파일을 만들고 지우므로 사이에 사라질 수 있다
        try:
            return p.stat().st_mtime
        except OSError:
            return 0
    files.sort(key=mtime, reverse=True)
    log = vault / "wiki" / "log.md"
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-6:] if log.is_file() else []
    return {"path": str(vault), "ok": True, "counts": counts,
            "recent": [str(p.relative_to(vault)).replace("\\", "/") for p in files[:6]], "log": tail}


class Hub:
    """SSE 구독자와 화면 상태. ponytail: 전역 잠금 하나로 한 번에 한 작업 — 여러 사용자가 생기면 큐로."""

    def __init__(self):
        self.clients: list[queue.Queue] = []
        self.recent = deque(maxlen=50)
        self.deck = deque(maxlen=20)
        self.audio: dict[int, bytes] = {}
        self.voice = "대기"
        self.busy = threading.Lock()
        self.n = 0
        self.gpu = None
        self.current = None  # 진행 중 작업 {q, steps, start} — 창을 새로 열어도 이어 보이게

    def send(self, ev: dict):
        self.recent.append(ev)
        for q in list(self.clients):
            q.put(ev)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 에서 SO_REUSEADDR 이면 두 번째 서버가 같은 포트를 조용히 잡아 요청이 엉뚱한 쪽으로 간다
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, engine, vault, port, stt, tts, cfg_file):
        super().__init__(("127.0.0.1", port), Handler)
        self.engine, self.vault, self.stt, self.tts, self.cfg_file = engine, vault, stt, tts, cfg_file
        self.hub = Hub()
        p = self.server_address[1]
        self.hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
        self.ack_cache: dict[str, bytes] = {}
        self.ack_i = 0
        self.restart = respawn
        threading.Thread(target=self.tick, daemon=True).start()
        threading.Thread(target=self.warm_acks, daemon=True).start()

    def next_ack(self) -> str:
        t = ACKS[self.ack_i % len(ACKS)]
        self.ack_i += 1
        return t

    def ack_wav(self, text: str) -> bytes:
        if text not in self.ack_cache:
            self.ack_cache[text] = self.tts(text)
        return self.ack_cache[text]

    def warm_acks(self):
        """맞장구를 미리 합성해 둔다 — TTS 모델 로드(수 초)도 여기서 끝낸다."""
        for t in ACKS:
            try:
                self.ack_wav(t)
            except Exception:
                return

    def play(self, wav: bytes):
        h = self.hub
        h.n += 1
        h.audio[h.n] = wav
        h.audio.pop(h.n - 10, None)
        h.send({"type": "audio", "url": f"/say/{h.n}.wav"})

    def snapshot(self) -> dict:
        h = self.hub
        plan = self.vault / "output" / "jarvis" / "plans" / f"{date.today()}.md"
        cur = h.current
        return {"type": "state", "voice": h.voice, "deck": list(h.deck),
                "vitals": {"cpu": psutil.cpu_percent(), "ram": psutil.virtual_memory().percent, "gpu": h.gpu},
                "vault": vault_state(self.vault),
                "plan": plan.read_text(encoding="utf-8", errors="replace")[:2000] if plan.is_file() else None,
                "current": cur and {"q": cur["q"], "steps": list(cur["steps"]), "elapsed": int(time.time() - cur["start"])}}

    def tick(self):
        while True:
            try:  # 한 번 실패로 스레드가 죽으면 HUD 가 조용히 멈춘다
                self.hub.gpu = gpu()
                if self.hub.clients:
                    self.hub.send(self.snapshot())
            except Exception:
                pass
            time.sleep(TICK)

    def set_voice(self, v: str):
        self.hub.voice = v
        self.hub.send({"type": "voice", "voice": v})

    def work(self, text: str | None, audio: bytes | None = None):
        """백그라운드에서: (음성이면 받아쓰기) → 엔진 → 답 → 음성. 호출 전에 busy 를 잡아 둔다."""
        h = self.hub
        try:
            if audio is not None:
                self.set_voice("받아쓰는 중")
                try:
                    text = self.stt(audio)
                except Exception as e:  # 모델 로드·디코드 실패
                    h.send({"type": "error", "text": f"음성 인식 실패: {e}"})
                    return
                if not text:
                    h.send({"type": "error", "text": "못 알아들었습니다. 다시 말씀해 주세요."})
                    return
            h.current = {"q": text, "steps": [], "start": time.time()}
            h.send({"type": "heard", "text": text})
            if audio is not None:  # 화면을 안 보는 음성 사용자에게 알아들었다고 먼저 말한다
                try:
                    self.play(self.ack_wav(self.next_ack()))
                except Exception:
                    pass
            self.set_voice("생각 중")
            try:
                a = self.engine.ask(text, on_step=lambda s: (h.current["steps"].append(s),
                                                             h.send({"type": "step", "text": s})))
            except Exception as e:
                h.send({"type": "error", "text": f"엔진 오류: {e}"})
                return
            h.deck.append({"t": datetime.now().strftime("%H:%M"), "q": text, "a": a.text, "ok": a.ok})
            try:
                append_log(self.vault, text, a.text, a.ok, "음성" if audio is not None else "입력")
            except OSError as e:  # 기록 실패가 답을 막지 않게 — 화면에만 알린다
                h.send({"type": "error", "text": f"대화 기록 실패: {e}"})
            h.send({"type": "answer", "text": a.text, "say": a.say, "ok": a.ok})
            self.set_voice("말하는 중")
            # 문장마다 합성해 바로 보낸다 — 첫 문장이 나머지 합성을 기다리지 않는다(HUD 가 차례대로 재생)
            for sentence in split_sentences(a.say):
                try:
                    wav = self.tts(sentence)
                except Exception as e:
                    h.send({"type": "error", "text": f"음성 합성 실패: {e}"})
                    return
                self.play(wav)
        finally:
            h.current = None
            self.set_voice("대기")
            h.busy.release()


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *a):
        pass

    def reply(self, code: int, body=b"", ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def guard(self, post: bool) -> bool:
        """다른 웹 페이지(CSRF)·DNS 리바인딩 차단."""
        ok = self.headers.get("Host") in self.server.hosts
        if post:
            ok = ok and self.headers.get("Origin", "").removeprefix("http://") in self.server.hosts
        if not ok:
            self.reply(403, {"error": "forbidden"})
        return ok

    def do_GET(self):
        if not self.guard(False):
            return
        s, path = self.server, self.path.split("?")[0]
        if path == "/":
            self.reply(200, HUD.read_bytes(), "text/html; charset=utf-8")
        elif path == "/state":
            self.reply(200, s.snapshot())
        elif path.startswith("/say/") and path.endswith(".wav"):
            wav = s.hub.audio.get(int(path[5:-4]) if path[5:-4].isdigit() else -1)
            self.reply(200, wav, "audio/wav") if wav else self.reply(404)
        elif path == "/events":
            self.events()
        else:
            self.reply(404)

    def events(self):
        q = queue.Queue()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.server.hub.clients.append(q)
        try:
            q.put(self.server.snapshot())
            while True:
                try:
                    ev = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            self.server.hub.clients.remove(q)

    def do_POST(self):
        if not self.guard(True):
            return
        s = self.server
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if not 0 <= n <= MAX_BODY:
            return self.reply(413, {"error": "요청 크기가 잘못됐습니다"})
        body = self.rfile.read(n)
        if self.path in ("/vault", "/ask"):
            try:
                data = json.loads(body or b"{}")
                assert isinstance(data, dict)
            except (ValueError, AssertionError):
                return self.reply(400, {"error": "잘못된 요청"})
        if self.path == "/vault":
            path = str(data.get("path", "")).strip().strip('"')
            if reason := config.check_vault(path):
                return self.reply(400, {"error": reason})
            path = os.path.abspath(path)  # 엔진은 brain/ 에서 돈다
            cfg = config.load(s.cfg_file)
            cfg["vault"] = path
            config.save(cfg, s.cfg_file)
            s.vault = s.engine.vault = Path(path)
            s.engine.session = None
            return self.reply(200, {"ok": True})
        if self.path == "/restart":
            self.reply(202, {"ok": True})
            threading.Thread(target=s.restart, args=(s,), daemon=True).start()
            return
        if self.path not in ("/ask", "/listen"):
            return self.reply(404)
        if reason := config.check_vault(str(s.vault)):
            return self.reply(503, {"error": f"금고 경로를 읽을 수 없습니다 — {reason}"})
        if self.path == "/ask":
            text = str(data.get("text", "")).strip()
            if not text:
                return self.reply(400, {"error": "빈 질문"})
            args = (text,)
        else:
            args = (None, body)
        if not s.hub.busy.acquire(blocking=False):
            return self.reply(409, {"error": "처리 중"})
        try:
            threading.Thread(target=s.work, args=args, daemon=True).start()
        except RuntimeError:
            s.hub.busy.release()
            raise
        self.reply(202, {"ok": True})


def make_server(engine, vault: Path, port: int = PORT, stt=voice.transcribe, tts=voice.speak,
                cfg_file: Path = config.CONFIG) -> Server:
    return Server(engine, vault, port, stt, tts, cfg_file)


def open_window(url: str):
    edge = shutil.which("msedge") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    try:
        subprocess.Popen([edge, f"--app={url}", "--window-size=1400,860"])
    except OSError:
        import webbrowser
        webbrowser.open(url)


def respawn(srv):
    """HUD 의 재시작 — 새 서버를 창 없이 띄우고 바로 끝난다. 기존 HUD 창이 다시 붙으며 새로고침한다.
    srv.shutdown() 을 먼저 부르면 main 스레드가 끝나 이 스레드가 새 서버를 띄우기 전에 프로세스가 죽는다 —
    새 서버가 먼저 뜨고, 포트는 이 프로세스가 끝나며 놓는다(새 서버는 그동안 다시 시도한다)."""
    time.sleep(0.3)  # 202 응답이 먼저 나가게
    env = {**os.environ, "JARVIS_NO_BROWSER": "1", "JARVIS_RESTARTED": "1"}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 7  # SW_SHOWMINNOACTIVE — 로그 콘솔은 작업 표시줄에 최소화
    subprocess.Popen([sys.executable, "-m", "jarvis.server"], cwd=config.ROOT, env=env,
                     creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=si)
    os._exit(0)


def main():
    vault = config.ensure_vault()
    url = f"http://127.0.0.1:{PORT}/"
    # 재시작 직후에는 옛 서버가 포트를 놓는 중일 수 있다 — 잠깐 다시 시도한다
    tries = 20 if os.environ.pop("JARVIS_RESTARTED", None) else 1
    for i in range(tries):
        try:
            srv = make_server(Engine(vault), vault)
            break
        except OSError:
            if i < tries - 1:
                time.sleep(0.5)
    else:
        print(f"JARVIS 가 이미 실행 중입니다 — 기존 HUD 창을 엽니다: {url}")
        if not os.environ.get("JARVIS_NO_BROWSER"):
            open_window(url)
        return
    print(f"JARVIS HUD: {url}  (금고: {vault})")
    if not os.environ.get("JARVIS_NO_BROWSER"):
        open_window(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
