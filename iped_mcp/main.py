"""
Servidor MCP para explorar casos IPED processados.

Usa a API REST padrão do IPED (iped.engine.webapi.Main), que acompanha todo caso
em iped/lib. O servidor sobe a API sozinho com o Java do próprio caso (iped/jre)
na primeira chamada de ferramenta, ou se conecta a uma API já em execução
(--api-url). Funciona para qualquer tipo de evidência (imagem de disco, UFDR,
pastas...), pois lê o índice do caso, e não o sleuth.db.

Uso:
    iped-mcp --case-path /caminho/do/caso [--case-path /outro/caso]
    iped-mcp --api-url http://127.0.0.1:8080
"""

import argparse
import asyncio
import atexit
import json
import os
import re
import shlex
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from iped_mcp.config import CaseConfig
from iped_mcp.webapi import IPEDAPIError, IPEDWebAPI, WebAPIServer

app = MCPServer(
    "iped-mcp",
    instructions=(
        "Ferramentas para analisar casos IPED processados (qualquer evidência: imagem de disco, UFDR, "
        "pastas). Comece com get_case_info e get_stats. Se nenhum caso estiver carregado, carregue com "
        "add_case(path) o caminho informado pelo usuário (raiz do caso, a pasta que contém 'iped/'), "
        "ou peça esse caminho a ele. Para trocar de caso, use close_case antes do add_case. As buscas usam a sintaxe de consulta do IPED "
        "(Lucene): palavras soltas buscam no texto e metadados; campos como category:\"Phone Calls\", "
        "type:jpg, deleted:true, carved:true, name:WhatsApp*, hash:<md5>, parentId:<id>, "
        "size:[1000000 TO *], modified:[2026-04-01 TO 2026-04-30]; combine com AND/OR/NOT. "
        "Em nomes de campo, escape ':' e '-' com '\\' (ex.: ufed\\:Source:WhatsApp, sha\\-256:<hash>). "
        "Cada item é identificado por (source_id, id); source_id pode ser omitido quando há um só caso."
    ),
)

MAX_DETAILED = 500
MAX_IDS = 20000

# Campos de get_document resumidos nas listagens
SUMMARY_FIELDS = (
    "name", "path", "category", "type", "contentType", "size", "deleted", "carved", "isDir",
    "hasChildren", "subitem", "created", "modified", "accessed", "changed", "hash", "sha-1",
    "sha-256", "parentId", "textSize",
)
SUMMARY_PREFIXES = ("Communication:",)
INT_FIELDS = {"size", "parentId", "textSize"}
BOOL_FIELDS = {"deleted", "carved", "isDir", "hasChildren", "subitem"}


class MCPState:
    """API do IPED em uso (iniciada sob demanda) e caches de consultas."""

    def __init__(self):
        self.cases: list[CaseConfig] = []
        self.api_url: Optional[str] = None
        self.java_opts: list[str] = []
        self.allow_write = False
        self.server: Optional[WebAPIServer] = None
        self.api: Optional[IPEDWebAPI] = None
        self._lock = threading.Lock()
        self._search_cache: "OrderedDict[tuple, list]" = OrderedDict()
        self._stats_cache: Optional[dict] = None

    def get_api(self) -> IPEDWebAPI:
        with self._lock:
            if self.api is not None:
                return self.api
            if self.api_url:
                api = IPEDWebAPI(self.api_url)
                if not api.is_alive():
                    raise IPEDAPIError(f"API do IPED não responde em {self.api_url}")
            elif self.cases:
                self.server = WebAPIServer(self.cases, java_opts=self.java_opts)
                api = self.server.start()
            else:
                raise IPEDAPIError("Nenhum caso carregado. Use add_case(path) com a raiz do caso "
                                   "(pasta que contém 'iped/'), ou inicie com --case-path/--api-url.")
            self.api = api
            return api

    def search_ids(self, query: str, source_id: Optional[str]) -> list[tuple[str, int]]:
        key = (query, source_id)
        if key in self._search_cache:
            self._search_cache.move_to_end(key)
            return self._search_cache[key]
        ids = self.get_api().search(query, source_id)
        self._search_cache[key] = ids
        while len(self._search_cache) > 16:
            self._search_cache.popitem(last=False)
        return ids

    def clear_caches(self):
        self._search_cache.clear()
        self._stats_cache = None

    def stop_api(self):
        """Encerrar a API iniciada por este servidor; ela sobe de novo na próxima chamada."""
        with self._lock:
            if self.server:
                self.server.stop()
            self.server = None
            self.api = None
        self.clear_caches()

    def shutdown(self):
        if self.server:
            self.server.stop()


state = MCPState()
atexit.register(state.shutdown)


# =====================================================================
# Helpers
# =====================================================================

def _json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


async def _call(fn, *args, **kwargs):
    """Rodar uma chamada bloqueante à API sem travar o loop, convertendo erros."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except (IPEDAPIError, ValueError, OSError) as e:
        raise ToolError(str(e)) from e


def _resolve_source(source_id: Optional[str]) -> str:
    if source_id:
        return source_id
    sources = state.get_api().list_sources()
    if len(sources) == 1:
        return sources[0]["id"]
    ids = ", ".join(s["id"] for s in sources)
    raise ValueError(f"Há {len(sources)} casos carregados; informe source_id ({ids}).")


def _flat(values):
    if not isinstance(values, list):
        return values
    values = [v for v in values if v != ""]
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def _summarize(doc: dict) -> dict:
    props = doc.get("properties", {})
    out = {"source_id": doc.get("source"), "id": doc.get("id")}
    for field in SUMMARY_FIELDS:
        value = _flat(props.get(field))
        if value is None:
            continue
        # A API devolve tudo como texto; tipar os campos mais usados
        if field in INT_FIELDS and isinstance(value, str) and value.isdigit():
            value = int(value)
        elif field in BOOL_FIELDS and value in ("true", "false"):
            value = value == "true"
        out[field] = value
    for field, values in props.items():
        if field.startswith(SUMMARY_PREFIXES):
            value = _flat(values)
            if value is not None:
                out[field] = value
    if doc.get("bookmarks"):
        out["bookmarks"] = doc["bookmarks"]
    return out


def _fetch_summaries(refs: list[tuple[str, int]]) -> list[dict]:
    api = state.get_api()

    def one(ref):
        try:
            return _summarize(api.get_document(*ref))
        except IPEDAPIError as e:
            return {"source_id": ref[0], "id": ref[1], "error": str(e)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, refs))


def _page(refs: list[tuple[str, int]], limit: int, offset: int, details: bool) -> dict:
    limit = max(1, min(int(limit), MAX_DETAILED if details else MAX_IDS))
    offset = max(0, int(offset))
    page = refs[offset:offset + limit]
    items = _fetch_summaries(page) if details else [{"source_id": s, "id": i} for s, i in page]
    return {"total": len(refs), "returned": len(items), "offset": offset, "limit": limit, "items": items}


def _require_write():
    if not state.allow_write:
        raise ToolError("Ferramenta de escrita desabilitada. Inicie o servidor com --allow-write.")


def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:150] or "item"


# =====================================================================
# Caso e fontes
# =====================================================================

@app.tool()
async def get_case_info() -> str:
    """Informações do(s) caso(s) carregado(s): fontes, URL da API, Java usado e log."""
    def info():
        if state.api is None and not state.api_url and not state.cases:
            return {"sources": [], "message": "Nenhum caso carregado. Use add_case(path) com a raiz "
                                              "do caso (pasta que contém 'iped/')."}
        api = state.get_api()
        data = {
            "api_url": api.base_url,
            "sources": api.list_sources(),
            "allow_write": state.allow_write,
        }
        if state.server:
            data["java"] = state.server.java
            data["webapi_log"] = str(state.server.log_path)
        return data
    return _json(await _call(info))


@app.tool()
async def list_sources() -> str:
    """Listar as fontes (casos IPED) carregadas na API, com id e caminho."""
    return _json(await _call(lambda: state.get_api().list_sources()))


@app.tool()
async def add_case(path: str, source_id: Optional[str] = None) -> str:
    """Carregar outro caso IPED processado na API (para consultar vários casos juntos).

    Args:
        path: Raiz do caso (pasta que contém 'iped/') ou a própria pasta 'iped/'.
        source_id: Identificador da fonte (default: nome da pasta do caso).
    """
    def add():
        case = CaseConfig(path, source_id)
        if state.api is None and not state.api_url:
            state.cases.append(case)
            try:
                state.get_api()
            except Exception:
                state.cases.remove(case)
                raise
        else:
            state.get_api().add_source(case.source_id, str(case.case_root))
            if not state.api_url:
                # Guardado para a API subir com ele de novo após um close_case
                state.cases.append(case)
        state.clear_caches()
        return {"added": case.to_source(), "sources": state.get_api().list_sources()}
    return _json(await _call(add))


@app.tool()
async def close_case(source_id: Optional[str] = None) -> str:
    """Descarregar um caso (ou todos), por exemplo para abrir outro em seguida com add_case.

    A API do IPED é encerrada e, se restarem casos, sobe de novo com eles na próxima chamada.

    Args:
        source_id: Caso a descarregar (veja list_sources). Omitido: descarrega todos.
    """
    def close():
        if state.api_url:
            raise ValueError("A API do IPED é externa (--api-url); os casos dela não são descarregados por aqui.")
        if source_id is None:
            closed, remaining = state.cases, []
        else:
            closed = [c for c in state.cases if c.source_id == source_id]
            if not closed:
                loaded = [c.source_id for c in state.cases]
                raise ValueError(f"Caso não carregado: {source_id!r}. Carregados: {loaded}")
            remaining = [c for c in state.cases if c.source_id != source_id]
        state.cases = remaining
        state.stop_api()
        return {"closed": [c.to_source() for c in closed], "remaining": [c.to_source() for c in remaining]}
    return _json(await _call(close))


@app.tool()
async def list_categories() -> str:
    """Listar as categorias de itens do caso (ex.: 'phone calls', 'contacts', 'videos')."""
    return _json(await _call(lambda: state.get_api().list_categories()))


@app.tool()
async def get_stats(refresh: bool = False) -> str:
    """Estatísticas do caso: total de itens, apagados, recuperados (carved) e contagem por categoria.
    A primeira chamada pode levar alguns segundos em casos grandes; o resultado fica em cache.

    Args:
        refresh: Recalcular em vez de usar o cache.
    """
    def stats():
        if state._stats_cache and not refresh:
            return state._stats_cache
        api = state.get_api()
        categories = api.list_categories()

        def count(q):
            return len(api.search(q))

        with ThreadPoolExecutor(max_workers=4) as pool:
            totals = dict(zip(("total_items", "deleted", "carved"),
                              pool.map(count, ("*", "deleted:true", "carved:true"))))
            per_cat = list(pool.map(lambda c: count(f'category:"{c}"'), categories))
        by_category = sorted(
            ({"category": c, "count": n} for c, n in zip(categories, per_cat)),
            key=lambda x: -x["count"],
        )
        result = {"sources": api.list_sources(), **totals, "by_category": by_category}
        state._stats_cache = result
        return result
    return _json(await _call(stats))


# =====================================================================
# Busca
# =====================================================================

@app.tool()
async def search(query: str, source_id: Optional[str] = None, limit: int = 50,
                 offset: int = 0, details: bool = True) -> str:
    """Buscar itens com a sintaxe de consulta do IPED (Lucene), com paginação.

    Exemplos: 'pix', '"conta corrente"', 'category:"Phone Calls"', 'type:jpg AND deleted:true',
    'name:WhatsApp*', 'size:[1000000 TO *]', 'modified:[2026-04-01 TO 2026-04-30]', '*' (tudo).

    Args:
        query: Consulta IPED/Lucene.
        source_id: Limitar a uma fonte (default: todas).
        limit: Itens por página (máx. 500 com details, 20000 sem).
        offset: Deslocamento para paginação.
        details: True traz nome, caminho, categoria, datas, hash etc. de cada item; False só os IDs.
    """
    refs = await _call(state.search_ids, query, source_id)
    return _json({"query": query, **await _call(_page, refs, limit, offset, details)})


@app.tool()
async def count(query: str, source_id: Optional[str] = None) -> str:
    """Contar os itens que casam com uma consulta IPED (mesma sintaxe de search).

    Args:
        query: Consulta IPED/Lucene.
        source_id: Limitar a uma fonte (default: todas).
    """
    refs = await _call(state.search_ids, query, source_id)
    return _json({"query": query, "count": len(refs)})


@app.tool()
async def search_by_category(category: str, source_id: Optional[str] = None,
                             limit: int = 50, offset: int = 0) -> str:
    """Listar itens de uma categoria (veja list_categories).

    Args:
        category: Nome da categoria (ex.: 'Phone Calls', 'contacts'; não diferencia maiúsculas).
        source_id: Limitar a uma fonte.
        limit: Itens por página (máx. 500).
        offset: Deslocamento para paginação.
    """
    query = 'category:"{}"'.format(category.replace('"', '\\"'))
    refs = await _call(state.search_ids, query, source_id)
    return _json({"query": query, **await _call(_page, refs, limit, offset, True)})


@app.tool()
async def search_by_hash(hash_value: str, source_id: Optional[str] = None) -> str:
    """Buscar itens por hash MD5 (32), SHA-1 (40) ou SHA-256 (64 caracteres hexadecimais).

    Args:
        hash_value: Hash a procurar.
        source_id: Limitar a uma fonte.
    """
    h = hash_value.strip().lower()
    field = {32: "hash", 40: "sha\\-1", 64: "sha\\-256"}.get(len(h))
    if not field or not re.fullmatch(r"[0-9a-f]+", h):
        raise ToolError("Hash inválido: informe MD5, SHA-1 ou SHA-256 em hexadecimal.")
    refs = await _call(state.search_ids, f"{field}:{h}", source_id)
    return _json(await _call(_page, refs, MAX_DETAILED, 0, True))


@app.tool()
async def get_children(doc_id: int, source_id: Optional[str] = None,
                       limit: int = 100, offset: int = 0) -> str:
    """Listar os filhos diretos de um item (conteúdo de pasta, anexos, itens extraídos de um container).

    Args:
        doc_id: ID do item pai.
        source_id: Fonte do item (pode ser omitida com um só caso).
        limit: Itens por página (máx. 500).
        offset: Deslocamento para paginação.
    """
    def children():
        src = _resolve_source(source_id)
        return _page(state.search_ids(f"parentId:{int(doc_id)}", src), limit, offset, True)
    return _json(await _call(children))


# =====================================================================
# Documentos
# =====================================================================

@app.tool()
async def get_document(doc_id: int, source_id: Optional[str] = None, all_properties: bool = False) -> str:
    """Metadados de um item. Por padrão traz um resumo; all_properties=True traz todas as
    propriedades indexadas (ex.: campos ufed:*, EXIF, metadados de comunicação).

    Args:
        doc_id: ID do item.
        source_id: Fonte do item (pode ser omitida com um só caso).
        all_properties: Retornar todas as propriedades.
    """
    def get():
        doc = state.get_api().get_document(_resolve_source(source_id), doc_id)
        if not all_properties:
            return _summarize(doc)
        props = {k: _flat(v) for k, v in sorted(doc.get("properties", {}).items())}
        return {"source_id": doc.get("source"), "id": doc.get("id"),
                "bookmarks": doc.get("bookmarks", []), "selected": doc.get("selected"),
                "properties": {k: v for k, v in props.items() if v is not None}}
    return _json(await _call(get))


@app.tool()
async def get_document_text(doc_id: int, source_id: Optional[str] = None,
                            offset: int = 0, max_chars: int = 20000) -> str:
    """Texto extraído de um item (documentos, mensagens, bancos SQLite, plists...).

    Args:
        doc_id: ID do item.
        source_id: Fonte do item (pode ser omitida com um só caso).
        offset: Posição inicial em caracteres (para ler textos longos em partes).
        max_chars: Máximo de caracteres retornados (default 20000, máx. 200000).
    """
    max_chars = max(1, min(int(max_chars), 200_000))

    def text():
        src = _resolve_source(source_id)
        return {"source_id": src, "id": doc_id, **state.get_api().get_text(src, doc_id, max(0, int(offset)), max_chars)}
    return _json(await _call(text))


@app.tool()
async def export_document(doc_id: int, output_dir: str, source_id: Optional[str] = None,
                          thumbnail: bool = False) -> str:
    """Exportar o conteúdo bruto de um item (ou sua miniatura) para uma pasta local.
    Calcula MD5/SHA-256 do arquivo gravado e confere com o hash registrado no caso.

    Args:
        doc_id: ID do item.
        output_dir: Pasta de destino.
        source_id: Fonte do item (pode ser omitida com um só caso).
        thumbnail: Exportar a miniatura (JPEG) em vez do conteúdo.
    """
    def export():
        api = state.get_api()
        src = _resolve_source(source_id)
        props = api.get_document(src, doc_id).get("properties", {})
        name = _flat(props.get("name")) or "item"
        if isinstance(name, list):
            name = name[0]
        filename = f"{doc_id}_{_safe_filename(str(name))}" + ("_thumb.jpg" if thumbnail else "")
        result = api.save_content(src, doc_id, Path(output_dir) / filename, thumb=thumbnail)
        result.update({"source_id": src, "id": doc_id, "name": name})
        expected = _flat(props.get("hash"))
        if not thumbnail and isinstance(expected, str):
            result["expected_md5"] = expected.lower()
            result["hash_ok"] = expected.lower() == result["md5"]
        return result
    return _json(await _call(export))


# =====================================================================
# Bookmarks
# =====================================================================

@app.tool()
async def list_bookmarks() -> str:
    """Listar os marcadores (bookmarks) do caso, com a quantidade de itens em cada um."""
    def bookmarks():
        api = state.get_api()
        return [{"name": b, "count": len(api.get_bookmark_docs(b))} for b in api.list_bookmarks()]
    return _json(await _call(bookmarks))


@app.tool()
async def get_bookmark_items(bookmark: str, limit: int = 50, offset: int = 0, details: bool = True) -> str:
    """Listar os itens de um marcador.

    Args:
        bookmark: Nome do marcador.
        limit: Itens por página (máx. 500 com details).
        offset: Deslocamento para paginação.
        details: True traz os metadados resumidos de cada item.
    """
    def items():
        return {"bookmark": bookmark,
                **_page(state.get_api().get_bookmark_docs(bookmark), limit, offset, details)}
    return _json(await _call(items))


@app.tool()
async def create_bookmark(bookmark: str) -> str:
    """Criar um marcador no caso (requer --allow-write).

    Args:
        bookmark: Nome do marcador.
    """
    _require_write()
    await _call(lambda: state.get_api().create_bookmark(bookmark))
    return _json({"created": bookmark})


@app.tool()
async def add_to_bookmark(bookmark: str, doc_ids: list[int], source_id: Optional[str] = None) -> str:
    """Adicionar itens a um marcador existente (requer --allow-write).

    Args:
        bookmark: Nome do marcador.
        doc_ids: IDs dos itens.
        source_id: Fonte dos itens (pode ser omitida com um só caso).
    """
    _require_write()

    def add():
        src = _resolve_source(source_id)
        state.get_api().add_to_bookmark(bookmark, [(src, i) for i in doc_ids])
        return {"bookmark": bookmark, "added": len(doc_ids)}
    return _json(await _call(add))


@app.tool()
async def remove_from_bookmark(bookmark: str, doc_ids: list[int], source_id: Optional[str] = None) -> str:
    """Remover itens de um marcador (requer --allow-write).

    Args:
        bookmark: Nome do marcador.
        doc_ids: IDs dos itens.
        source_id: Fonte dos itens (pode ser omitida com um só caso).
    """
    _require_write()

    def remove():
        src = _resolve_source(source_id)
        state.get_api().remove_from_bookmark(bookmark, [(src, i) for i in doc_ids])
        return {"bookmark": bookmark, "removed": len(doc_ids)}
    return _json(await _call(remove))


# =====================================================================
# Entry point
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="IPED MCP Server (API REST padrão do IPED)")
    parser.add_argument("--case-path", action="append", default=[],
                        help="Caso IPED processado (raiz do caso ou pasta iped/). Pode repetir.")
    parser.add_argument("--api-url", default=os.environ.get("IPED_API_URL"),
                        help="Usar uma API do IPED já em execução em vez de iniciar uma")
    parser.add_argument("--java-opts", default=os.environ.get("IPED_JAVA_OPTS", ""),
                        help="Opções da JVM da API, ex.: \"-Xmx8g\"")
    parser.add_argument("--allow-write", action="store_true",
                        help="Habilitar ferramentas que alteram marcadores do caso")
    parser.add_argument("--eager", action="store_true",
                        help="Iniciar a API já na inicialização, não na primeira chamada")
    args = parser.parse_args()

    try:
        state.cases = [CaseConfig(p) for p in args.case_path]
    except ValueError as e:
        parser.error(str(e))
    state.api_url = args.api_url
    state.java_opts = shlex.split(args.java_opts, posix=os.name != "nt")
    state.allow_write = args.allow_write
    if args.eager and (state.cases or state.api_url):
        state.get_api()
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
