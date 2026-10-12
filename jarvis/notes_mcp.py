"""MCP stdio server `jarvis-notes` — the engine's only way into the work notes. Usage: notes_mcp.py <vault>"""
import sys
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from jarvis.notes import Notes


def make_server(vault: Path) -> MCPServer:
    notes = Notes(vault)
    srv = MCPServer("jarvis-notes")

    @srv.tool()
    def notes_search(query: str, limit: int = 8) -> dict:
        """업무 노트(raw/evernote)에서 질의와 관련된 노트 위치를 찾는다. 본문은 주지 않는다.
        결과의 sensitive 가 true 인 노트는 민감 문서라 위치만 알려 줄 수 있다."""
        return notes.search(query, limit)

    @srv.tool()
    def notes_read(path: str, start: int = 1, end: int = 0) -> dict:
        """업무 노트의 줄 구간(최대 200줄)을 줄 번호와 함께 읽는다. 비밀처럼 보이는 줄은 [가림] 으로 나온다.
        path 는 notes_search 가 준 금고 기준 경로. end=0 이면 start 부터 200줄."""
        return notes.read(path, start, end)

    @srv.tool()
    def notes_list(folder: str) -> dict:
        """업무 노트 폴더(raw/evernote 아래 상대 경로, 예: 과제프로젝트)의 노트 목록과 줄 수, 민감 여부. 본문은 주지 않는다."""
        return notes.list(folder)

    return srv


if __name__ == "__main__":
    make_server(Path(sys.argv[1])).run("stdio")
