"""Testes do IPED MCP.

Os testes unitários usam um servidor HTTP falso que imita a API REST do IPED.
O teste de integração sobe a API real sobre um caso processado e só roda com
IPED_TEST_CASE=/caminho/do/caso.
"""

import hashlib
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import pytest

CONTENT = b"conteudo do arquivo"
DOCS = {
    1: {"name": ["pasta"], "category": ["Folders"], "isDir": ["true"], "path": ["ev/pasta"],
        "hasChildren": ["true"], "parentId": ["0"]},
    2: {"name": ["nota.txt"], "category": ["Text Documents"], "type": ["txt"], "size": ["19"],
        "path": ["ev/pasta/nota.txt"], "parentId": ["1"], "deleted": ["false"], "changed": [""],
        "hash": [hashlib.md5(CONTENT).hexdigest().upper()], "ufed:Extra": ["x"]},
    3: {"name": ["Call_1"], "category": ["Phone Calls"], "parentId": ["1"], "deleted": ["true"],
        "Communication:From": ["+5531999990000"]},
}
QUERIES = {
    "*": [1, 2, 3],
    "pix": [2],
    "deleted:true": [3],
    "carved:true": [],
    "parentId:1": [2, 3],
    'category:"folders"': [1],
    'category:"text documents"': [2],
    'category:"phone calls"': [3],
    "hash:" + hashlib.md5(CONTENT).hexdigest(): [2],
}


class FakeIPED(BaseHTTPRequestHandler):
    bookmarks = {"Relevantes": [2]}

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ids(self, ids):
        return {"data": [{"source": "caso", "ids": ids}] if ids else []}

    def do_GET(self):
        url = urlparse(self.path)
        path = unquote(url.path)
        if path == "/sources":
            return self._send(200, {"data": [{"id": "caso", "path": "/casos/caso"}]})
        if path == "/categories":
            return self._send(200, {"data": ["folders", "phone calls", "text documents"]})
        if path == "/search":
            q = parse_qs(url.query).get("q", [""])[0]
            if q not in QUERIES and q.lower() not in QUERIES:
                return self._send(500, b"<html><body>Request failed.</body></html>", "text/html")
            return self._send(200, self._ids(QUERIES.get(q, QUERIES.get(q.lower()))))
        if path == "/bookmarks":
            return self._send(200, {"data": list(self.bookmarks)})
        m = re.fullmatch(r"/bookmarks/(.+)", path)
        if m:
            return self._send(200, self._ids(self.bookmarks.get(m.group(1), [])))
        m = re.fullmatch(r"/sources/caso/docs/(\d+)(/\w+)?", path)
        if m and int(m.group(1)) in DOCS:
            doc_id, kind = int(m.group(1)), m.group(2)
            if kind is None:
                return self._send(200, {"source": "caso", "id": doc_id, "luceneId": doc_id,
                                        "properties": DOCS[doc_id], "selected": False,
                                        "bookmarks": [b for b, ids in self.bookmarks.items() if doc_id in ids]})
            if kind == "/text":
                return self._send(200, "texto extraído ção ".encode() * 1000, "text/plain; charset=UTF-8")
            if kind == "/content":
                return self._send(200, CONTENT, "application/octet-stream")
        return self._send(404, b"<html><body>Not Found</body></html>", "text/html")

    def do_PUT(self):
        m = re.fullmatch(r"/bookmarks/(.+)/(add|remove)", unquote(self.path))
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ids = self.bookmarks.setdefault(m.group(1), [])
        for d in body:
            if m.group(2) == "add":
                ids.append(d["id"])
            else:
                ids.remove(d["id"])
        self._send(200, {})


@pytest.fixture
def fake_api():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeIPED)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def server(fake_api):
    from iped_mcp import main

    main.state = main.MCPState()
    main.state.api_url = fake_api
    yield main
    main.state = main.MCPState()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _load(text):
    return json.loads(text)


# =====================================================================
# Configuração
# =====================================================================

def test_case_config(tmp_path):
    from iped_mcp.config import CaseConfig

    (tmp_path / "caso" / "iped" / "index").mkdir(parents=True)
    (tmp_path / "caso" / "iped" / "lib").mkdir()
    for p in (tmp_path / "caso", tmp_path / "caso" / "iped"):
        case = CaseConfig(str(p))
        assert case.case_root == tmp_path / "caso"
        assert case.source_id == "caso"
        assert case.to_source() == {"id": "caso", "path": str(tmp_path / "caso")}
    with pytest.raises(ValueError):
        CaseConfig(str(tmp_path))


def test_server_command(tmp_path):
    from iped_mcp.config import CaseConfig
    from iped_mcp.webapi import WebAPIServer

    (tmp_path / "iped" / "index").mkdir(parents=True)
    (tmp_path / "iped" / "lib").mkdir()
    srv = WebAPIServer([CaseConfig(str(tmp_path), "c1")], port=9999, java="java", java_opts=["-Xmx2g"])
    cmd = srv.command()
    assert cmd[:4] == ["java", "-Xmx2g", "-cp", str(tmp_path / "iped" / "lib" / "*")]
    assert "iped.engine.webapi.Main" in cmd and "--port=9999" in cmd
    sources = json.loads((srv.workdir / "sources.json").read_text(encoding="utf-8"))
    assert sources == [{"id": "c1", "path": str(tmp_path)}]


# =====================================================================
# Cliente HTTP
# =====================================================================

def test_client(fake_api, tmp_path):
    from iped_mcp.webapi import IPEDAPIError, IPEDWebAPI

    api = IPEDWebAPI(fake_api)
    assert api.is_alive()
    assert api.search("*") == [("caso", 1), ("caso", 2), ("caso", 3)]
    assert api.get_document("caso", 2)["properties"]["name"] == ["nota.txt"]
    text = api.get_text("caso", 2, offset=6, max_chars=10)
    assert text["text"] == "extraído ç" and text["truncated"]
    saved = api.save_content("caso", 2, tmp_path / "out.bin")
    assert (tmp_path / "out.bin").read_bytes() == CONTENT
    assert saved["md5"] == hashlib.md5(CONTENT).hexdigest()
    with pytest.raises(IPEDAPIError, match="Request failed"):
        api.search("AND ((")
    assert not IPEDWebAPI("http://127.0.0.1:1").is_alive()


# =====================================================================
# Ferramentas MCP
# =====================================================================

@pytest.mark.anyio
async def test_search_tools(server):
    data = _load(await server.search("*", limit=2))
    assert data["total"] == 3 and data["returned"] == 2
    assert data["items"][1]["name"] == "nota.txt"
    assert data["items"][1]["size"] == 19 and data["items"][1]["deleted"] is False
    assert "changed" not in data["items"][1]          # valores vazios são omitidos
    assert "ufed:Extra" not in data["items"][1]        # só campos do resumo
    data = _load(await server.search("*", offset=2, details=False))
    assert data["items"] == [{"source_id": "caso", "id": 3}]
    assert _load(await server.count("deleted:true"))["count"] == 1

    calls = _load(await server.search_by_category("Phone Calls"))
    assert calls["items"][0]["Communication:From"] == "+5531999990000"
    children = _load(await server.get_children(1))
    assert [c["id"] for c in children["items"]] == [2, 3]
    by_hash = _load(await server.search_by_hash(hashlib.md5(CONTENT).hexdigest().upper()))
    assert by_hash["items"][0]["id"] == 2


@pytest.mark.anyio
async def test_stats_and_documents(server, tmp_path):
    stats = _load(await server.get_stats())
    assert stats["total_items"] == 3 and stats["deleted"] == 1 and stats["carved"] == 0
    assert {c["category"]: c["count"] for c in stats["by_category"]}["phone calls"] == 1

    doc = _load(await server.get_document(2))
    assert doc["bookmarks"] == ["Relevantes"] and doc["category"] == "Text Documents"
    full = _load(await server.get_document(2, all_properties=True))
    assert full["properties"]["ufed:Extra"] == "x"

    text = _load(await server.get_document_text(2, max_chars=7))
    assert text["text"] == "texto e"

    exported = _load(await server.export_document(2, str(tmp_path)))
    assert exported["hash_ok"] is True
    assert (tmp_path / "2_nota.txt").read_bytes() == CONTENT


@pytest.mark.anyio
async def test_bookmarks_and_errors(server):
    from mcp.server.mcpserver.exceptions import ToolError

    assert _load(await server.list_bookmarks()) == [{"name": "Relevantes", "count": 1}]
    items = _load(await server.get_bookmark_items("Relevantes"))
    assert items["items"][0]["name"] == "nota.txt"

    with pytest.raises(ToolError, match="allow-write"):
        await server.add_to_bookmark("Relevantes", [3])
    server.state.allow_write = True
    await server.add_to_bookmark("Relevantes", [3])
    assert _load(await server.list_bookmarks())[0]["count"] == 2
    await server.remove_from_bookmark("Relevantes", [3])

    with pytest.raises(ToolError, match="Request failed"):
        await server.search("AND ((")
    with pytest.raises(ToolError, match="404"):
        await server.get_document(999)
    with pytest.raises(ToolError, match="Hash inválido"):
        await server.search_by_hash("xyz")


@pytest.mark.anyio
async def test_no_case_configured():
    from mcp.server.mcpserver.exceptions import ToolError
    from iped_mcp import main

    main.state = main.MCPState()
    with pytest.raises(ToolError, match="Nenhum caso"):
        await main.list_sources()
    info = _load(await main.get_case_info())
    assert info["sources"] == [] and "add_case" in info["message"]
    with pytest.raises(ToolError, match="Não é um caso IPED"):
        await main.add_case("/nao/existe")


@pytest.mark.anyio
async def test_close_case(tmp_path, server):
    from mcp.server.mcpserver.exceptions import ToolError
    from iped_mcp import main
    from iped_mcp.config import CaseConfig

    # API externa (--api-url): não descarrega
    with pytest.raises(ToolError, match="externa"):
        await server.close_case()

    for name in ("c1", "c2"):
        (tmp_path / name / "iped" / "index").mkdir(parents=True)
        (tmp_path / name / "iped" / "lib").mkdir()
    main.state = main.MCPState()
    main.state.cases = [CaseConfig(str(tmp_path / n)) for n in ("c1", "c2")]
    with pytest.raises(ToolError, match="Caso não carregado"):
        await main.close_case("c3")
    result = _load(await main.close_case("c1"))
    assert [c["id"] for c in result["closed"]] == ["c1"]
    assert [c["id"] for c in result["remaining"]] == ["c2"]
    result = _load(await main.close_case())
    assert [c["id"] for c in result["closed"]] == ["c2"] and result["remaining"] == []
    assert _load(await main.get_case_info())["sources"] == []


# =====================================================================
# Integração com um caso real
# =====================================================================

@pytest.mark.anyio
@pytest.mark.skipif(not os.environ.get("IPED_TEST_CASE"), reason="defina IPED_TEST_CASE")
async def test_real_case(tmp_path):
    from iped_mcp import main
    from iped_mcp.config import CaseConfig

    main.state = main.MCPState()
    main.state.cases = [CaseConfig(os.environ["IPED_TEST_CASE"])]
    try:
        info = _load(await main.get_case_info())
        assert len(info["sources"]) == 1
        stats = _load(await main.get_stats())
        assert stats["total_items"] > 0 and stats["by_category"]
        page = _load(await main.search("*", limit=5))
        assert page["returned"] == 5
        first = page["items"][0]
        assert _load(await main.get_document(first["id"]))["id"] == first["id"]
        files = _load(await main.search("type:txt OR type:json", limit=1))
        if files["items"]:
            item = files["items"][0]
            exported = _load(await main.export_document(item["id"], str(tmp_path)))
            assert exported.get("hash_ok", True)
    finally:
        main.state.shutdown()
        main.state = main.MCPState()
