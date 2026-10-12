import json

from jarvis import config


def test_check_vault_missing_path_gives_reason(tmp_path):
    assert config.check_vault(str(tmp_path / "nope"))


def test_check_vault_ok_creates_output_dir(tmp_path):
    assert config.check_vault(str(tmp_path)) is None
    assert (tmp_path / "output" / "jarvis").is_dir()


def test_check_vault_empty_string():
    assert config.check_vault("")


def test_ensure_vault_reasks_until_valid_and_saves(tmp_path):
    cfg = tmp_path / "config.local.json"
    cfg.write_text(json.dumps({"vault": str(tmp_path / "gone")}), encoding="utf-8")
    good = tmp_path / "vault"
    good.mkdir()
    answers = iter([str(tmp_path / "still-wrong"), f'  "{good}"  '])
    asked = []

    def ask(prompt):
        asked.append(prompt)
        return next(answers)

    got = config.ensure_vault(ask=ask, out=lambda *_: None, cfg_file=cfg)
    assert got == good
    assert len(asked) == 2
    assert json.loads(cfg.read_text(encoding="utf-8"))["vault"] == str(good)


def test_ensure_vault_keeps_valid_saved_path_without_asking(tmp_path):
    cfg = tmp_path / "config.local.json"
    config.save({"vault": str(tmp_path)}, cfg)

    def ask(_):
        raise AssertionError("should not ask")

    assert config.ensure_vault(ask=ask, out=lambda *_: None, cfg_file=cfg) == tmp_path


def test_ensure_vault_saves_absolute_path(tmp_path, monkeypatch):
    (tmp_path / "v").mkdir()
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "c.json"
    got = config.ensure_vault(ask=lambda _: "v", out=lambda *_: None, cfg_file=cfg)
    assert got.is_absolute() and json.loads(cfg.read_text(encoding="utf-8"))["vault"] == str(tmp_path / "v")


def test_read_access_keeps_absolute_entries_only(tmp_path, capsys):
    f = tmp_path / "read-access.json"
    f.write_text(json.dumps({"read": ["F:/python_works/**/prior-work.jsonl", "relative/dir", "", 3]}), encoding="utf-8")
    assert config.read_access(f) == ["F:/python_works/**/prior-work.jsonl"]
    assert "relative/dir" in capsys.readouterr().out


def test_read_access_missing_or_broken_file_is_empty(tmp_path):
    assert config.read_access(tmp_path / "none.json") == []
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert config.read_access(tmp_path / "bad.json") == []
