"""Work notes (raw/evernote) — search via the graphify index plus literal text, read line ranges with secrets masked.

This is the only door from the engine into the work notes: the engine's own Read on raw/evernote stays denied.
Whatever these functions return goes to Anthropic, so sensitive notes never yield body text.
"""
import json
import re
from datetime import date
from pathlib import Path

NOTES_DIR = "raw/evernote"
MAX_LINES = 200
MAX_RESULTS = 20

# Copied from graphify validation_poc.py — not imported,
# because the graphify path differs per PC. Keep the two in step when either changes.
_GOOGLE_KEY = re.compile(r"AIza[0-9A-Za-z_-]{20,}")
_GITHUB_TOKEN = re.compile(r"gh[pousr]_[0-9A-Za-z]{20,}")
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|personal access token|password|passwd|credential|"
    r"recovery codes?|two.factor|\b2fa\b|"
    r"\bpwd\b|\bpw\s*[:=]|passcode|client[_ -]?secret|secret[_ -]?key|\bsk-[A-Za-z0-9_-]{20,})"
)
_KOREAN_SENSITIVE = re.compile("비밀번호|패스워드|암호|토큰|인증.?키|API.?키|복구.?코드|비번")
_SENSITIVE_PATH = ("장기 저장", "내 정보", "회사 정보", "api key", "recovery code", "2fa", "credential",
                   "password", "token",
                   "_계정")  # 계정 정보는 '…_계정.md' 로 분리한다 — 내용과 상관없이 민감

# Line-level masking for notes that pass the whole-note check: "label: value" assignments and raw keys.
_SECRET_LINE = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|token|secret|client[_ -]?secret|password|passwd|pwd|pw|passcode|"
    r"비밀번호|비번|암호|패스워드|토큰|인증키)\b\s*[:=]\s*\S"
    r"|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[0-9A-Za-z]{20,}|\bsk-[A-Za-z0-9_-]{20,}|PRIVATE KEY-----"
)


# 웹 페이지 스크랩만 든 폴더 — 기술 글의 'password'·'token' 같은 낱말로 통째로 막지 않는다.
# '_계정' 파일은 여기서도 민감이고, 비밀처럼 보이는 줄은 읽을 때 그대로 가린다.
TRUSTED_DIRS = ("raw/evernote/스크랩/",
                "raw/evernote/연구노트/",  # 계정은 '_계정' 파일로 분리됨
                # 장기 저장은 개인 정보 폴더 — 업무용 두 파일만 개별 허용, 나머지는 위치만 알린다.
                "raw/evernote/장기 저장/S3 Browser Pro 사용방법.md",
                "raw/evernote/장기 저장/회사 정보.md")


def is_sensitive(rel: str, text: str) -> bool:
    """graphify 와 같은 판정 — 걸리면 노트 전체를 본문 없이 위치만 다룬다."""
    if rel.startswith(TRUSTED_DIRS):
        return "_계정" in rel
    p = rel.lower().replace(" ", "")
    if any(m.replace(" ", "") in p for m in _SENSITIVE_PATH):  # '내 정보' 와 '내정보' 를 같이 잡는다
        return True
    return any(r.search(text) for r in (_GOOGLE_KEY, _GITHUB_TOKEN, _PRIVATE_KEY, _SENSITIVE_TEXT, _KOREAN_SENSITIVE))


def redact(lines: list[str]) -> tuple[list[str], int]:
    out = ["[가림]" if _SECRET_LINE.search(line) else line for line in lines]
    return out, sum(a != b for a, b in zip(out, lines))


# 민감 노트를 "무엇이 들어 있나" 로 찾을 때 쓰는 고정 분류어. 질의의 다른 낱말(값)은 민감 본문과 대조하지 않는다.
CATEGORY_WORDS = ("주소", "전화", "연락처", "휴대폰", "계좌", "은행", "이메일", "메일", "생년월일", "주민번호", "여권",
                  "차량", "카드", "계정", "아이디", "접속", "서버", "VPN", "와이파이", "wifi", "인증서", "라이선스")
_FIELD = re.compile(r"^\s*(?:[-*]\s*)?([^:=|\n]{1,20}?)\s*[:=]")
_HEADING = re.compile(r"^#{1,6}\s+(.{1,40})")


def field_labels(text: str) -> list[tuple[int, str]]:
    """(줄 번호, 항목 이름) — '주소 : …' 의 '주소', 소제목. 값은 뽑지 않는다. 숫자 위주 라벨은 값일 수 있어 버린다."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = _HEADING.match(line) or _FIELD.match(line)
        if m:
            lab = m.group(1).strip()
            if re.search(r"[A-Za-z가-힣]", lab) and sum(c.isdigit() for c in lab) * 2 < len(lab):
                out.append((i, lab))
    return out


def _grams(text: str) -> set[str]:
    s = re.sub(r"\s+", "", text.lower())
    return {s[i:i + w] for w in (2, 3) for i in range(max(0, len(s) - w + 1))}


class Notes:
    def __init__(self, vault: Path):
        self.vault = vault
        self.root = vault / NOTES_DIR
        self.graph_file = vault / "output" / "graphify" / "graph.json"
        self._graph, self._graph_mtime = None, None

    def _index(self) -> list[dict] | None:
        try:
            m = self.graph_file.stat().st_mtime
            if m != self._graph_mtime:
                nodes = json.loads(self.graph_file.read_text(encoding="utf-8"))["nodes"]
                self._graph = [n for n in nodes if str(n.get("source_file", "")).startswith(NOTES_DIR + "/")]
                self._graph_mtime = m
        except (OSError, ValueError, KeyError, TypeError):
            self._graph, self._graph_mtime = None, None
        return self._graph

    def _text(self, rel: str) -> str:
        return (self.vault / rel).read_text(encoding="utf-8", errors="replace")

    def search(self, query: str, limit: int = 8) -> dict:
        limit = max(1, min(int(limit), MAX_RESULTS))
        # 민감 노트 위치(locations)는 일반 결과(results)와 칸을 다투지 않게 따로 낸다 — 흔한 낱말에 밀려 사라지지 않게
        results, locations, seen, sens = [], [], set(), {}

        def sensitive(rel, node=None):
            if rel not in sens:
                try:
                    sens[rel] = bool(node and node.get("sensitive_locator")) or is_sensitive(rel, self._text(rel))
                except OSError:
                    sens[rel] = True  # 읽을 수 없으면 안전한 쪽으로
            return sens[rel]

        index = self._index()
        q = _grams(query)
        best = {}
        for n in index or []:
            score = len(q & _grams(str(n.get("label", "")))) / max(1, len(q))
            rel = n["source_file"]
            if score and score > best.get(rel, (0,))[0]:
                best[rel] = (score, n)
        for rel, (_, n) in sorted(best.items(), key=lambda kv: (-kv[1][0], kv[0])):
            s = sensitive(rel, n)
            # 민감 노트는 색인 라벨 대신 파일 이름만 — 라벨에 계정 ID 같은 값이 들어 있을 수 있다
            (locations if s else results).append({"path": rel, "lines": n.get("source_location", ""),
                                                   "label": Path(rel).stem if s else n.get("label", ""),
                                                   "via": "index", "sensitive": s})
            seen.add(rel)

        terms = [t.lower() for t in query.split() if t]
        words = [t for t in terms if len(t) >= 2] or terms
        need = max(1, (len(words) + 1) // 2)  # "우리 집 주소" 같은 군말이 섞여도 절반 이상 맞으면 후보
        text_hits = []
        for f in sorted(self.root.rglob("*.md")) if self.root.is_dir() and terms else []:
            rel = f.relative_to(self.vault).as_posix()
            if rel in seen:
                continue
            title = f.stem.lower()
            if sensitive(rel):  # 본문 값은 검색하지 않는다 — 값 추측(있나 없나) 질의를 막는다
                if all(t in title for t in terms):
                    locations.insert(0, {"path": rel, "lines": "", "label": f.stem, "via": "text", "sensitive": True})
                    continue
                body = self._text(rel)
                hit = None
                for i, lab in field_labels(body):  # 항목 이름(주소·계좌번호 등)으로만 찾고 위치만 준다
                    if any(t in lab.lower() or lab.lower() in t for t in words if len(t) >= 2):
                        hit = (i, lab, "label")
                        break
                if not hit:  # 평문 속 분류어 — "집주소는" 의 '주소' 처럼 질의에 든 고정 분류어만 본문과 대조한다
                    lines = body.lower().splitlines()
                    for cw in CATEGORY_WORDS:
                        if any(cw.lower() in t for t in words):
                            i = next((n for n, line in enumerate(lines, 1) if cw.lower() in line), 0)
                            if i:
                                hit = (i, cw, "category")
                                break
                if hit:
                    locations.append({"path": rel, "lines": f"L{hit[0]}", "label": f"{f.stem} · {hit[1]}",
                                      "via": hit[2], "sensitive": True})
                continue
            best = (sum(t in title for t in words), 1)
            for i, line in enumerate(self._text(rel).splitlines(), 1):
                score = sum(t in line.lower() for t in words)
                if score > best[0]:
                    best = (score, i)
            if best[0] >= need:
                text_hits.append((-best[0], rel, {"path": rel, "lines": f"L{best[1]}", "label": f.stem,
                                                  "via": "text", "sensitive": False}))
        results += [hit for *_, hit in sorted(text_hits, key=lambda h: h[:2])]

        idx_date = date.fromtimestamp(self._graph_mtime).isoformat() if index is not None else None
        order = {"text": 0, "index": 1, "label": 2, "category": 3}  # 제목 일치 > 색인 > 항목 이름 > 분류어
        # 같은 단계에서는 이름부터 정보 모음(내 정보·회사 정보 …)인 노트를 앞에 둔다
        info = lambda x: not any(m.replace(" ", "") in x["path"].lower().replace(" ", "") for m in _SENSITIVE_PATH)
        locations.sort(key=lambda x: (order[x["via"]], info(x)))
        return {"index_date": idx_date, "results": results[:limit], "locations": locations[:10]}

    def list(self, folder: str) -> dict:
        """업무 노트 폴더(raw/evernote 기준 상대) 안의 노트 목록 — 위키 정리용. 본문은 주지 않는다."""
        d = (self.root / folder.replace("\\", "/").strip("/")).resolve()
        if not d.is_relative_to(self.root.resolve()) or not d.is_dir():
            return {"error": f"업무 노트 폴더가 아닙니다: {folder}"}
        out = []
        for f in sorted(d.rglob("*.md")):
            rel = f.relative_to(self.vault).as_posix()
            text = self._text(rel)
            out.append({"path": rel, "lines": len(text.splitlines()), "sensitive": is_sensitive(rel, text)})
        return {"folder": d.relative_to(self.vault).as_posix(), "notes": out}

    def read(self, path: str, start: int = 1, end: int = 0) -> dict:
        rel = str(path).replace("\\", "/").strip()
        if not rel.startswith(NOTES_DIR + "/") or not rel.endswith(".md"):
            return {"error": f"업무 노트({NOTES_DIR}/ 아래 .md)만 읽을 수 있습니다: {rel}"}
        f = (self.vault / rel).resolve()
        if not f.is_relative_to(self.root.resolve()) or not f.is_file():
            return {"error": f"노트를 찾을 수 없습니다: {rel}"}
        text = f.read_text(encoding="utf-8", errors="replace")
        if is_sensitive(rel, text):
            return {"error": f"민감 문서라 내용을 열 수 없습니다. 위치만 알려 주세요: {rel}"}
        lines = text.splitlines()
        start = max(1, int(start))
        end = start + MAX_LINES - 1 if int(end) <= 0 else min(int(end), start + MAX_LINES - 1)
        end = min(end, len(lines))
        part, n = redact(lines[start - 1:end]) if start <= end else ([], 0)
        return {"path": rel, "start": start, "end": end, "total_lines": len(lines), "redacted": n,
                "text": "\n".join(f"{start + i}: {line}" for i, line in enumerate(part))}
