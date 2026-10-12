"""금고 경로 설정 — PC마다 다르므로 config.local.json(git 제외)에 둔다."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.local.json"
ALLOWED = ("raw", "ai-research", "wiki", "output")  # 엔진이 읽을 수 있는 금고 폴더
READ_ACCESS = ROOT / "read-access.json"  # 금고 밖에서 엔진이 읽기만 할 폴더·파일 패턴(git 제외, PC마다 다름)


def check_vault(path: str) -> str | None:
    """문제 없으면 None, 있으면 한국어 이유."""
    if not path:
        return "경로가 비어 있습니다"
    p = Path(path)
    if not p.is_dir():
        return f"폴더가 없거나 읽을 수 없습니다: {path}"
    try:
        next(p.iterdir(), None)
        (p / "output" / "jarvis").mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"읽기/쓰기 실패: {e}"
    return None


def load(cfg_file: Path = CONFIG) -> dict:
    try:
        return json.loads(cfg_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cfg: dict, cfg_file: Path = CONFIG) -> None:
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_vault(ask=input, out=print, cfg_file: Path = CONFIG) -> Path:
    """저장된 금고 경로가 통과할 때까지 묻고, 바뀌면 저장한다."""
    cfg = load(cfg_file)
    path = cfg.get("vault", "")
    while reason := check_vault(path):
        out(f"[금고] {reason}")
        path = ask("Obsidian 금고 경로를 입력하세요: ").strip().strip('"').strip()
    path = os.path.abspath(path)  # 엔진은 brain/ 에서 돌므로 상대경로면 범위가 어긋난다
    if cfg.get("vault") != path:
        cfg["vault"] = path
        save(cfg, cfg_file)
    return Path(path)


def read_access(cfg_file: Path = READ_ACCESS) -> list[str]:
    """read-access.json 의 "read" 항목 중 절대경로만. 파일이 없거나 깨졌으면 빈 목록(추가 허용 없음)."""
    try:
        entries = json.loads(cfg_file.read_text(encoding="utf-8")).get("read", [])
    except (OSError, ValueError, AttributeError):
        return []
    ok = []
    for e in entries if isinstance(entries, list) else []:
        if isinstance(e, str) and e and Path(e).is_absolute():
            ok.append(e)
        else:  # 상대경로는 엔진 작업 폴더(brain/) 기준으로 풀려 범위가 어긋난다
            print(f"[read-access] 절대경로가 아니라 무시합니다: {e!r}")
    return ok
