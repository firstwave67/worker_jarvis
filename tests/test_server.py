import http.client
import json
import shutil
import threading
import time

import pytest

from jarvis import server
from jarvis.engine import Answer


class FakeEngine:
    def __init__(self, vault):
        self.vault, self.calls, self.gate = vault, [], threading.Event()
        self.gate.set()

    def ask(self, text, on_step=None):
        self.calls.append(text)
        self.audio_before = sum(e["type"] == "audio" for e in self.hub.recent) if hasattr(self, "hub") else 0
        if on_step:
            on_step("스킬: plan")
        self.gate.wait(5)
        return Answer(getattr(self, "answer_text", "본문"), "요약입니다", True, "s1")


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"
    for d in ("raw", "wiki", "output"):
        (vault / d).mkdir(parents=True)
    (vault / "wiki" / "log.md").write_text("## [2026-10-09 10:00] ingest | a | b\n", encoding="utf-8")
    eng = FakeEngine(vault)
    stt = {"text": "안녕"}
    srv = server.make_server(eng, vault, port=0, stt=lambda b: stt["text"], tts=lambda t: b"RIFFfake",
                             cfg_file=tmp_path / "cfg.json")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    yield dict(srv=srv, eng=eng, vault=vault, port=port, stt=stt, tmp=tmp_path)
    srv.shutdown()


def req(env, method, path, body=None, origin="same", host=None):
    c = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=10)
    h = {"Host": host or f"127.0.0.1:{env['port']}"}
    if origin == "same":
        h["Origin"] = f"http://127.0.0.1:{env['port']}"
    elif origin:
        h["Origin"] = origin
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def wait_for(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_post_without_or_foreign_origin_is_403(env):
    assert req(env, "POST", "/ask", {"text": "x"}, origin=None)[0] == 403
    assert req(env, "POST", "/ask", {"text": "x"}, origin="http://evil.example")[0] == 403
    assert env["eng"].calls == []


def test_foreign_host_header_is_403(env):
    assert req(env, "GET", "/state", host="evil.example:9300")[0] == 403


def test_ask_runs_engine_and_serves_say_audio(env):
    assert req(env, "POST", "/ask", {"text": "질문"})[0] == 202
    assert wait_for(lambda: any(e["type"] == "audio" for e in env["srv"].hub.recent))
    ev = [e for e in env["srv"].hub.recent if e["type"] in ("answer", "audio")]
    assert ev[0]["say"] == "요약입니다"
    st, wav = req(env, "GET", ev[1]["url"])
    assert st == 200 and wav == b"RIFFfake"


def test_second_ask_while_busy_is_409(env):
    env["eng"].gate.clear()
    assert req(env, "POST", "/ask", {"text": "1"})[0] == 202
    assert wait_for(lambda: env["eng"].calls)
    assert req(env, "POST", "/ask", {"text": "2"})[0] == 409
    env["eng"].gate.set()


def test_missing_vault_is_503(env):
    shutil.rmtree(env["vault"])
    st, body = req(env, "POST", "/ask", {"text": "x"})
    assert st == 503 and "금고" in body.decode()
    assert env["eng"].calls == []


def test_empty_transcript_skips_engine(env):
    env["stt"]["text"] = ""
    assert req(env, "POST", "/listen", b"\x00\x01")[0] == 202
    assert wait_for(lambda: any(e["type"] == "error" for e in env["srv"].hub.recent))
    assert "못 알아들었습니다" in [e for e in env["srv"].hub.recent if e["type"] == "error"][0]["text"]
    assert env["eng"].calls == []


def test_listen_transcript_goes_to_engine(env):
    assert req(env, "POST", "/listen", b"\x00\x01")[0] == 202
    assert wait_for(lambda: env["eng"].calls == ["안녕"])


def test_state_snapshot(env):
    st, body = req(env, "GET", "/state")
    s = json.loads(body)
    assert st == 200
    assert set(s) >= {"vitals", "vault", "plan", "deck", "voice"}
    assert s["vault"]["ok"] and s["vault"]["counts"]["wiki"] == 1
    assert "ingest" in s["vault"]["log"][-1]


def test_vault_change_validates_and_saves(env):
    assert req(env, "POST", "/vault", {"path": str(env["tmp"] / "nope")})[0] == 400
    new = env["tmp"] / "v2"
    new.mkdir()
    assert req(env, "POST", "/vault", {"path": str(new)})[0] == 200
    assert env["eng"].vault == new
    assert json.loads((env["tmp"] / "cfg.json").read_text(encoding="utf-8"))["vault"] == str(new)


def test_events_stream_sends_state_first(env):
    c = http.client.HTTPConnection("127.0.0.1", env["port"], timeout=5)
    c.request("GET", "/events", headers={"Host": f"127.0.0.1:{env['port']}"})
    r = c.getresponse()
    assert r.getheader("Content-Type") == "text/event-stream"
    line = r.fp.readline().decode()
    assert line.startswith("data: ") and json.loads(line[6:])["type"] == "state"
    c.close()


def test_hud_page_served(env):
    st, body = req(env, "GET", "/")
    assert st == 200 and b"<title>" in body


def test_bad_json_is_400(env):
    assert req(env, "POST", "/ask", b"{not json")[0] == 400
    assert req(env, "POST", "/ask", b"[1]")[0] == 400


def test_engine_exception_sends_error_and_frees_lock(env):
    def boom(text):
        raise RuntimeError("터짐")
    env["eng"].ask = boom
    assert req(env, "POST", "/ask", {"text": "x"})[0] == 202
    assert wait_for(lambda: any(e["type"] == "error" for e in env["srv"].hub.recent))
    assert wait_for(lambda: not env["srv"].hub.busy.locked())


def test_vault_change_resolves_relative_path(env, monkeypatch):
    (env["tmp"] / "rel").mkdir()
    monkeypatch.chdir(env["tmp"])
    assert req(env, "POST", "/vault", {"path": "rel"})[0] == 200
    assert env["eng"].vault.is_absolute()


def test_steps_are_broadcast(env):
    assert req(env, "POST", "/ask", {"text": "q"})[0] == 202
    assert wait_for(lambda: any(e["type"] == "step" and e["text"] == "스킬: plan" for e in env["srv"].hub.recent))


def test_voice_input_plays_ack_before_engine(env):
    env["eng"].hub = env["srv"].hub
    assert req(env, "POST", "/listen", b"\x00\x01")[0] == 202
    assert wait_for(lambda: env["eng"].calls == ["안녕"])
    assert env["eng"].audio_before == 1
    ack = [e for e in env["srv"].hub.recent if e["type"] == "audio"][0]
    assert req(env, "GET", ack["url"])[0] == 200


def test_text_input_has_no_ack(env):
    env["eng"].hub = env["srv"].hub
    assert req(env, "POST", "/ask", {"text": "q"})[0] == 202
    assert wait_for(lambda: env["eng"].calls == ["q"])
    assert env["eng"].audio_before == 0


def test_acks_rotate_and_are_cached(env):
    said = []
    env["srv"].tts = lambda t: said.append(t) or b"RIFFack"
    env["srv"].ack_cache.clear()
    picks = [env["srv"].next_ack() for _ in range(4)]
    assert picks[:3] == list(server.ACKS) and picks[3] == server.ACKS[0]
    for t in picks:
        env["srv"].ack_wav(t)
    assert said == list(server.ACKS)


def test_state_includes_current_work_while_busy(env):
    env["eng"].gate.clear()
    assert req(env, "POST", "/ask", {"text": "긴 작업"})[0] == 202
    assert wait_for(lambda: env["eng"].calls)
    cur = json.loads(req(env, "GET", "/state")[1])["current"]
    assert cur["q"] == "긴 작업" and cur["steps"] == ["스킬: plan"] and cur["elapsed"] >= 0
    env["eng"].gate.set()
    assert wait_for(lambda: json.loads(req(env, "GET", "/state")[1])["current"] is None)


def test_second_server_on_same_port_fails(env):
    """Windows 에서 SO_REUSEADDR 이면 두 번째 서버가 조용히 같은 포트를 잡는다."""
    with pytest.raises(OSError):
        server.make_server(env["eng"], env["vault"], port=env["port"], stt=lambda b: "", tts=lambda t: b"",
                           cfg_file=env["tmp"] / "c2.json")


def log_file(env):
    import socket
    from datetime import date
    return env["vault"] / "raw" / "jarvis-log" / f"{date.today()}-{server.safe_host(socket.gethostname())}.md"


def test_answers_are_logged_to_vault_with_secrets_masked(env):
    env["eng"].answer_text = "회의는 금요일\ntoken: SECRET-LOG-777"
    assert req(env, "POST", "/ask", {"text": "회의 언제야"})[0] == 202
    assert wait_for(lambda: log_file(env).is_file() and "금요일" in log_file(env).read_text(encoding="utf-8"))
    t = log_file(env).read_text(encoding="utf-8")
    assert "회의 언제야" in t and "입력" in t and "[가림]" in t and "SECRET-LOG-777" not in t


def test_log_appends_and_marks_voice(env):
    assert req(env, "POST", "/ask", {"text": "첫 질문"})[0] == 202
    assert wait_for(lambda: not env["srv"].hub.busy.locked() and log_file(env).is_file())
    assert req(env, "POST", "/listen", bytes([0, 1]))[0] == 202
    assert wait_for(lambda: log_file(env).read_text(encoding="utf-8").count("## ") == 2)
    t = log_file(env).read_text(encoding="utf-8")
    assert "첫 질문" in t and "안녕" in t and "음성" in t
    assert t.startswith("# JARVIS 대화 기록")


def test_say_is_spoken_sentence_by_sentence(env):
    said = []
    env["srv"].tts = lambda t: said.append(t) or b"RIFF" + t.encode()
    env["eng"].ask = lambda text, on_step=None: Answer("본문", "첫 문장입니다. 네. 둘째 문장입니다!", True, "s1")
    assert req(env, "POST", "/ask", {"text": "q"})[0] == 202
    assert wait_for(lambda: not env["srv"].hub.busy.locked() and len(said) == 2)
    assert said == ["첫 문장입니다. 네.", "둘째 문장입니다!"]
    urls = [e["url"] for e in env["srv"].hub.recent if e["type"] == "audio"]
    assert len(urls) == 2 and req(env, "GET", urls[0])[1].endswith("첫 문장입니다. 네.".encode())


def test_split_sentences_merges_short_fragments():
    assert server.split_sentences("하나입니다. 응. 셋째 문장! 마지막") == ["하나입니다. 응.", "셋째 문장! 마지막"]
    assert server.split_sentences("") == []


def test_restart_calls_restarter_after_reply(env):
    called = threading.Event()
    env["srv"].restart = lambda srv: called.set()
    assert req(env, "POST", "/restart")[0] == 202
    assert called.wait(5)


def test_restart_from_foreign_origin_is_403(env):
    called = threading.Event()
    env["srv"].restart = lambda srv: called.set()
    assert req(env, "POST", "/restart", origin="http://evil.example")[0] == 403
    assert req(env, "POST", "/restart", origin=None)[0] == 403
    assert not called.wait(0.3)
