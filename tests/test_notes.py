import json

import pytest

from jarvis import notes


@pytest.fixture
def vault(tmp_path):
    ev = tmp_path / "raw" / "evernote" / "업무"
    ev.mkdir(parents=True)
    body = [f"줄 {i}" for i in range(1, 301)]
    body[9] = "배포 일정은 금요일 오후"
    body[19] = "token: SECRET-INLINE-111"
    body[29] = "token 갱신 안내 메일을 보냈다"
    (ev / "회의록 🚀 10월.md").write_text("\n".join(body), encoding="utf-8")
    (ev / "VPN 접속.md").write_text("서버 vpn.example\npwd : SECRET-NOTE-222\n", encoding="utf-8")
    (tmp_path / "raw" / "memo.md").write_text("outside evernote\n", encoding="utf-8")
    g = tmp_path / "output" / "graphify"
    g.mkdir(parents=True)
    nodes = [
        {"label": "배포 일정", "source_file": "raw/evernote/업무/회의록 🚀 10월.md", "source_location": "L10",
         "evidence": "SHOULD-NOT-LEAK"},
        {"label": "VPN 접속 정보", "source_file": "raw/evernote/업무/VPN 접속.md", "source_location": "L2",
         "sensitive_locator": True},
        {"label": "배포 문서", "source_file": "wiki/x.md", "source_location": "L1"},
    ]
    (g / "graph.json").write_text(json.dumps({"nodes": nodes}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_is_sensitive_by_content_and_path():
    assert notes.is_sensitive("raw/evernote/a.md", "id/pw: x")
    assert notes.is_sensitive("raw/evernote/2FA 코드.md", "nothing")
    assert not notes.is_sensitive("raw/evernote/a.md", "그냥 회의 메모")


def test_redact_only_assignments_and_key_patterns():
    lines, n = notes.redact(["api_key = abc123", "password reset 안내", "token: zz9", "ghp_" + "a" * 30, "일반 줄"])
    assert lines == ["[가림]", "password reset 안내", "[가림]", "[가림]", "일반 줄"] and n == 3


def test_search_merges_index_and_text_without_evidence(vault):
    r = notes.Notes(vault).search("배포 일정")
    first = r["results"][0]
    assert first == {"path": "raw/evernote/업무/회의록 🚀 10월.md", "lines": "L10", "label": "배포 일정",
                     "via": "index", "sensitive": False}
    assert all(x["path"].startswith("raw/evernote/") for x in r["results"])
    assert "SHOULD-NOT-LEAK" not in json.dumps(r, ensure_ascii=False)
    assert r["index_date"]


def test_search_marks_sensitive_and_never_searches_its_body(vault):
    n = notes.Notes(vault)
    hit = [x for x in n.search("VPN")["locations"] if "VPN" in x["path"]][0]
    assert hit["sensitive"] is True and not any("VPN" in x["path"] for x in n.search("VPN")["results"])
    r = n.search("SECRET-NOTE-222")
    assert not any("VPN" in x["path"] for x in r["results"] + r["locations"])


def test_search_works_without_index(vault):
    (vault / "output" / "graphify" / "graph.json").write_text("{broken", encoding="utf-8")
    r = notes.Notes(vault).search("금요일")
    assert r["results"][0]["via"] == "text" and r["results"][0]["lines"] == "L10"
    assert r["index_date"] is None


def test_read_returns_numbered_redacted_range(vault):
    r = notes.Notes(vault).read("raw/evernote/업무/회의록 🚀 10월.md", 18, 31)
    assert r["start"] == 18 and r["end"] == 31 and r["total_lines"] == 300 and r["redacted"] == 1
    assert "SECRET-INLINE-111" not in r["text"]
    assert "30: token 갱신 안내 메일을 보냈다" in r["text"]


def test_read_caps_at_200_lines_and_clamps(vault):
    n = notes.Notes(vault)
    r = n.read("raw/evernote/업무/회의록 🚀 10월.md", -5, 0)
    assert r["start"] == 1 and r["end"] == 200
    assert n.read("raw/evernote/업무/회의록 🚀 10월.md", 999)["text"] == ""


@pytest.mark.parametrize("path", ["raw/memo.md", "raw/evernote/../memo.md", "/etc/passwd",
                                  "raw/evernote/업무/없는파일.md", "raw/evernote/업무"])
def test_read_refuses_outside_or_missing(vault, path):
    assert "error" in notes.Notes(vault).read(path)


def test_read_refuses_sensitive_note_with_location_only(vault):
    r = notes.Notes(vault).read("raw/evernote/업무/VPN 접속.md")
    assert "error" in r and "SECRET-NOTE-222" not in json.dumps(r, ensure_ascii=False)
    assert "VPN 접속.md" in r["error"]


def test_mcp_server_exposes_two_tools(vault):
    import asyncio
    from jarvis import notes_mcp
    srv = notes_mcp.make_server(vault)
    names = {t.name for t in asyncio.run(srv.list_tools())}
    assert names == {"notes_search", "notes_read", "notes_list"}


def test_sensitive_note_found_by_field_label_location_only(vault):
    (vault / "raw" / "evernote" / "내정보.md").write_text(
        "# 개인\n- 주소 : 서울시 가나구 다라로 123\n계좌번호: 110-222-333333\n1234: 숫자만 라벨\n", encoding="utf-8")
    n = notes.Notes(vault)
    hit = [x for x in n.search("우리 집 주소")["locations"] if x["path"].endswith("내정보.md")]
    assert hit == [{"path": "raw/evernote/내정보.md", "lines": "L2", "label": "내정보 · 주소", "via": "label",
                    "sensitive": True}]
    for q in ("서울시", "다라로 123", "110-222", "1234"):  # 값으로 떠보기는 걸리지 않는다
        r = n.search(q)
        assert not any(x["path"].endswith("내정보.md") for x in r["results"] + r["locations"]), q


def test_text_search_tolerates_filler_words(vault):
    r = notes.Notes(vault).search("우리 배포 일정 언제")
    assert any(x["path"].endswith("회의록 🚀 10월.md") for x in r["results"])


def test_sensitive_path_marker_ignores_spaces():
    assert notes.is_sensitive("raw/evernote/내정보2.md", "평범한 내용")


def test_sensitive_note_found_by_category_word_in_plain_text(vault):
    (vault / "raw" / "evernote" / "내 정보.md").write_text("개인 기록\n집주소 서울시 마바구 사아로 45\n", encoding="utf-8")
    n = notes.Notes(vault)
    hit = [x for x in n.search("우리 집주소는 어디 있지")["locations"] if x["path"].endswith("내 정보.md")]
    assert hit == [{"path": "raw/evernote/내 정보.md", "lines": "L2", "label": "내 정보 · 주소", "via": "category",
                    "sensitive": True}]
    for q in ("서울시 마바구", "사아로 45", "개인 기록"):
        r = n.search(q)
        assert not any(x["path"].endswith("내 정보.md") for x in r["results"] + r["locations"]), q


def test_sensitive_locations_do_not_compete_with_results(vault):
    ev = vault / "raw" / "evernote"
    for i in range(12):
        (ev / f"네트워크 {i}.md").write_text("내부 주소 설정 메모\n", encoding="utf-8")
    (ev / "내 정보.md").write_text("집주소 어딘가\n", encoding="utf-8")
    r = notes.Notes(vault).search("우리 집 주소")
    assert len(r["results"]) == 8
    assert any(x["path"].endswith("내 정보.md") for x in r["locations"])
    assert all(x["sensitive"] for x in r["locations"]) and not any(x["sensitive"] for x in r["results"])


def test_info_notes_rank_first_among_category_hits(vault):
    ev = vault / "raw" / "evernote"
    (ev / "a 공유기 설정.md").write_text("관리자 비밀번호 있음\n접속 주소 192.168.0.1\n", encoding="utf-8")
    (ev / "내 정보.md").write_text("집주소 어딘가\n", encoding="utf-8")
    locs = notes.Notes(vault).search("집 주소")["locations"]
    assert locs[0]["path"].endswith("내 정보.md")


def test_account_suffix_files_are_always_sensitive():
    assert notes.is_sensitive("raw/evernote/과제/고객사 AWS_계정.md", "평범해 보이는 내용 user1 / abcd1234")


def test_sensitive_index_hit_shows_file_name_not_node_label(vault):
    import json as _j
    g = vault / "output" / "graphify" / "graph.json"
    d = _j.loads(g.read_text(encoding="utf-8"))
    d["nodes"].append({"label": "AWS 계정 ID 123456789012", "source_file": "raw/evernote/업무/VPN 접속.md",
                       "source_location": "L1"})
    g.write_text(_j.dumps(d, ensure_ascii=False), encoding="utf-8")
    locs = notes.Notes(vault).search("AWS 계정")["locations"]
    assert locs and all("123456789012" not in x["label"] for x in locs)


def test_list_folder_marks_sensitive_and_counts_lines(vault):
    (vault / "raw" / "evernote" / "업무" / "서버_계정.md").write_text("a\nb\n", encoding="utf-8")
    r = notes.Notes(vault).list("업무")
    by = {x["path"].rsplit("/", 1)[1]: x for x in r["notes"]}
    assert by["서버_계정.md"]["sensitive"] and by["VPN 접속.md"]["sensitive"]
    assert by["회의록 🚀 10월.md"] == {"path": "raw/evernote/업무/회의록 🚀 10월.md", "lines": 300, "sensitive": False}
    assert "error" in notes.Notes(vault).list("../../wiki")
    assert "error" in notes.Notes(vault).list("없는폴더")


def test_scrap_folder_is_trusted_web_clippings_but_account_files_stay_sensitive():
    assert not notes.is_sensitive("raw/evernote/스크랩/Kerberos (protocol) - Wikipedia.md", "password ticket token")
    assert notes.is_sensitive("raw/evernote/스크랩/무엇_계정.md", "평범")
    assert notes.is_sensitive("raw/evernote/과제프로젝트/a.md", "password")  # 신뢰 폴더가 아니면 그대로


def test_trusted_folder_still_masks_secret_lines(vault):
    d = vault / "raw" / "evernote" / "스크랩"
    d.mkdir()
    (d / "웹 글.md").write_text("api_key = abc123xyz\nJWT 토큰 개념 설명\n", encoding="utf-8")
    r = notes.Notes(vault).read("raw/evernote/스크랩/웹 글.md")
    assert r["redacted"] == 1 and "abc123xyz" not in r["text"] and "토큰 개념" in r["text"]


def test_research_notes_trusted_except_account_files():
    assert not notes.is_sensitive("raw/evernote/연구노트/클라우드 접속 정보.md", "token api key")
    assert notes.is_sensitive("raw/evernote/연구노트/설치 설정_계정.md", "평범")


def test_only_two_work_notes_in_longterm_folder_are_trusted():
    assert not notes.is_sensitive("raw/evernote/장기 저장/S3 Browser Pro 사용방법.md", "설명")
    assert not notes.is_sensitive("raw/evernote/장기 저장/회사 정보.md", "회사 주소")
    for name in ("내 정보.md", "연락처.md", "개인 메모.md", "회사 정보_계정.md", "새 노트.md"):
        assert notes.is_sensitive(f"raw/evernote/장기 저장/{name}", "평범"), name
