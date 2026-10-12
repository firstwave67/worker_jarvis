import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from jarvis import engine


def test_perm_path_lowercase_drive_no_colon():
    assert engine.perm_path(Path(r"E:\GoogleDrive\Obsidian Vault")) == "//e/GoogleDrive/Obsidian Vault"


def test_settings_never_allow_unscoped_read_and_deny_bash(tmp_path):
    s = json.loads(engine.settings_json(tmp_path))
    assert not {"Read", "Glob", "Grep"} & set(s["permissions"]["allow"])
    assert "Bash" in s["permissions"]["deny"]
    assert s["permissions"]["defaultMode"] == "acceptEdits"


def test_build_cmd_adds_only_existing_allowed_dirs(tmp_path):
    (tmp_path / "wiki").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "evernote").mkdir()
    cmd = engine.build_cmd(tmp_path, None, None)
    dirs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--add-dir"]
    assert dirs == [str(tmp_path / "wiki"), str(tmp_path / "output")]
    assert "--resume" not in cmd and "--model" not in cmd
    assert Path(cmd[0]).stem.lower() == "claude" and cmd[1] == "-p"


def test_build_cmd_resume_and_model(tmp_path):
    cmd = engine.build_cmd(tmp_path, "abc", "sonnet")
    assert cmd[cmd.index("--resume") + 1] == "abc"
    assert cmd[cmd.index("--model") + 1] == "sonnet"


def test_split_say_takes_last_say_line():
    assert engine.split_say("본문\nSAY: 처음\n더\nSAY: 마지막") == ("본문\n더", "마지막")


def test_split_say_falls_back_to_first_sentence():
    assert engine.split_say("## 결과\n메일은 세 건입니다. 나머지는 생략.") == (
        "## 결과\n메일은 세 건입니다. 나머지는 생략.", "메일은 세 건입니다.")


def fake_run(outputs, calls):
    """stream-json 줄을 하나씩 on_line 으로 흘려보내는 가짜 claude."""
    def run(cmd, *, on_line, **kw):
        calls.append((cmd, kw))
        for line in outputs.pop(0).splitlines():
            on_line(line)
        return SimpleNamespace(returncode=0, stderr="")
    return run


def ok(result, sid="s1"):
    return json.dumps({"type": "result", "result": result, "session_id": sid, "is_error": False})


def tool(name, **inp):
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "thinking", "thinking": ""}, {"type": "tool_use", "name": name, "input": inp}]}})


def test_engine_resumes_session_then_resets_next_day(tmp_path):
    calls, days = [], [date(2026, 10, 9)]
    e = engine.Engine(tmp_path, run=fake_run([ok("가\nSAY: 하나"), ok("나"), ok("다")], calls),
                      today=lambda: days[0])
    a = e.ask("질문")
    assert (a.text, a.say, a.ok, a.session) == ("가", "하나", True, "s1")
    assert calls[0][1]["input"].startswith("[지금: ") and calls[0][1]["input"].endswith("\n질문") and calls[0][1]["cwd"] == engine.BRAIN
    e.ask("둘")
    assert "--resume" in calls[1][0]
    days[0] = date(2026, 10, 10)
    e.ask("셋")
    assert "--resume" not in calls[2][0]


def test_engine_non_json_output_is_error(tmp_path):
    e = engine.Engine(tmp_path, run=fake_run(["Please run /login"], []))
    a = e.ask("x")
    assert not a.ok and "login" in a.text


def test_engine_timeout_is_error(tmp_path):
    import subprocess

    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 300)

    a = engine.Engine(tmp_path, run=run).ask("x")
    assert not a.ok


def test_webfetch_not_allowed_to_block_exfiltration(tmp_path):
    s = json.loads(engine.settings_json(tmp_path))
    assert "WebFetch" not in s["permissions"]["allow"] and "WebFetch" in s["permissions"]["deny"]


def test_engine_non_dict_json_is_error(tmp_path):
    a = engine.Engine(tmp_path, run=fake_run(["null"], [])).ask("x")
    assert not a.ok


def test_engine_error_clears_session(tmp_path):
    bad = json.dumps({"type": "result", "result": "No conversation found", "session_id": "s1", "is_error": True})
    calls = []
    e = engine.Engine(tmp_path, run=fake_run([ok("가"), bad, ok("다")], calls))
    e.ask("1"); e.ask("2"); e.ask("3")
    assert "--resume" not in calls[2][0]


def test_build_cmd_streams_events(tmp_path):
    cmd = engine.build_cmd(tmp_path, None, None)
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd


def test_step_label_by_tool(tmp_path):
    v = tmp_path
    assert engine.step_label("Read", {"file_path": str(v / "wiki" / "_master-index.md")}, v) == "읽는 중: wiki/_master-index.md"
    assert engine.step_label("Write", {"file_path": str(v / "output" / "a.md")}, v) == "쓰는 중: output/a.md"
    assert engine.step_label("WebSearch", {"query": "Claude Code"}, v) == "웹 검색: Claude Code"
    assert engine.step_label("mcp__claude_ai_Gmail__search_threads", {"query": "x"}, v) == "메일 확인 중"
    assert engine.step_label("Skill", {"skill": "inbox"}, v) == "스킬: inbox"
    assert engine.step_label("Grep", {"pattern": "TIPA"}, v) == "찾는 중: TIPA"
    assert engine.step_label("ToolSearch", {"query": "select:WebSearch"}, v) is None


def test_ask_reports_steps_in_order(tmp_path):
    steps = []
    out = "\n".join([tool("Skill", skill="plan"), tool("ToolSearch", query="q"),
                     tool("Read", file_path=str(tmp_path / "wiki" / "x.md")), "not json", ok("끝\nSAY: 완료")])
    a = engine.Engine(tmp_path, run=fake_run([out], [])).ask("q", on_step=steps.append)
    assert a.ok and a.say == "완료"
    assert steps == ["스킬: plan", "읽는 중: wiki/x.md"]


def test_evernote_is_blocked_until_graphify_integration():
    s = json.loads(engine.settings_json(Path(r"E:\GoogleDrive\ObsidianVault")))
    deny = s["permissions"]["deny"]
    assert "Read(//e/GoogleDrive/ObsidianVault/raw/evernote/**)" in deny
    assert "Edit(//e/GoogleDrive/ObsidianVault/raw/evernote/**)" in deny


def test_build_cmd_attaches_notes_mcp_server(tmp_path):
    import sys
    cmd = engine.build_cmd(tmp_path, None, None)
    cfg = json.loads(cmd[cmd.index("--mcp-config") + 1])["mcpServers"]["jarvis-notes"]
    assert cfg["command"] == sys.executable
    assert cfg["args"] == [str(engine.ROOT / "jarvis" / "notes_mcp.py"), str(tmp_path)]
    assert cfg["env"]["PYTHONPATH"] == str(engine.ROOT)
    allow = json.loads(engine.settings_json(tmp_path))["permissions"]["allow"]
    assert {"mcp__jarvis-notes__notes_search", "mcp__jarvis-notes__notes_read"} <= set(allow)


def test_step_label_for_notes_tools(tmp_path):
    assert engine.step_label("mcp__jarvis-notes__notes_search", {"query": "배포 일정"}, tmp_path) == "노트 검색: 배포 일정"
    assert engine.step_label("mcp__jarvis-notes__notes_read", {"path": "raw/evernote/a/b.md"}, tmp_path) == "노트 읽는 중: raw/evernote/a/b.md"


def test_conversation_log_is_read_only_for_engine():
    deny = json.loads(engine.settings_json(Path(r"E:\GoogleDrive\ObsidianVault")))["permissions"]["deny"]
    assert "Edit(//e/GoogleDrive/ObsidianVault/raw/jarvis-log/**)" in deny
    assert "Read(//e/GoogleDrive/ObsidianVault/raw/jarvis-log/**)" not in deny


CRASH = "Bun v1.4.3 (eecfd55de) Windows x64\npanic(main thread): Segmentation fault at address 0x10E46209C1\noh no: Bun has crashed."


def test_startup_crash_is_retried_once(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CRASH_LOG", tmp_path / "crash.log")
    calls = []
    a = engine.Engine(tmp_path, run=fake_run([CRASH, ok("됐다\nSAY: 완료")], calls)).ask("q")
    assert a.ok and a.say == "완료" and len(calls) == 2
    assert "Segmentation fault" in (tmp_path / "crash.log").read_text(encoding="utf-8")


def test_repeated_crash_gives_short_message_not_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CRASH_LOG", tmp_path / "crash.log")
    calls = []
    a = engine.Engine(tmp_path, run=fake_run([CRASH, CRASH], calls)).ask("q")
    assert not a.ok and len(calls) == 2
    assert "panic" not in a.text and "Bun" not in a.text and "다시" in a.text


def test_crash_after_work_started_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "CRASH_LOG", tmp_path / "crash.log")
    calls = []
    started = json.dumps({"type": "system", "subtype": "init"})
    a = engine.Engine(tmp_path, run=fake_run([started + "\n" + CRASH], calls)).ask("q")
    assert not a.ok and len(calls) == 1


def test_notes_list_tool_allowed_and_labelled(tmp_path):
    allow = json.loads(engine.settings_json(tmp_path))["permissions"]["allow"]
    assert "mcp__jarvis-notes__notes_list" in allow
    assert engine.step_label("mcp__jarvis-notes__notes_list", {"folder": "과제프로젝트"}, tmp_path) == "노트 목록: 과제프로젝트"


def test_read_access_entries_become_read_only_allow_rules(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    s = json.loads(engine.settings_json(tmp_path, [str(d), "F:/python_works/**/prior-work.jsonl"]))["permissions"]
    assert f"Read({engine.perm_path(d)}/**)" in s["allow"]
    assert "Read(//f/python_works/**/prior-work.jsonl)" in s["allow"]
    assert not any(r.startswith(("Edit(", "Write(")) for r in s["allow"])
    assert "Bash" in s["deny"]  # deny rules stay


def test_build_cmd_reads_access_file_each_call_and_tells_engine(tmp_path, monkeypatch):
    f = tmp_path / "read-access.json"
    monkeypatch.setattr(engine, "READ_ACCESS", f)
    cmd = engine.build_cmd(tmp_path, None, None)
    assert "--append-system-prompt" not in cmd
    f.write_text(json.dumps({"read": ["F:/python_works/**/prior-work.jsonl"]}), encoding="utf-8")
    cmd = engine.build_cmd(tmp_path, None, None)
    assert "F:/python_works/**/prior-work.jsonl" in cmd[cmd.index("--append-system-prompt") + 1]
    assert "Read(//f/python_works/**/prior-work.jsonl)" in cmd[cmd.index("--settings") + 1]
