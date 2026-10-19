"""
Cliente e inicializador da API REST padrão do IPED (iped.engine.webapi.Main).

A API acompanha todo caso processado (iped/lib) e expõe o índice Lucene do caso:
    GET  /sources                              fontes (casos) carregadas
    GET  /search?q=<consulta IPED>&sourceID=   IDs dos itens que casam com a consulta
    GET  /sources/{src}/docs/{id}              propriedades (metadados) do item
    GET  /sources/{src}/docs/{id}/text         texto extraído
    GET  /sources/{src}/docs/{id}/content      conteúdo bruto
    GET  /sources/{src}/docs/{id}/thumb        miniatura
    GET  /categories                           categorias
    GET  /bookmarks, /bookmarks/{nome}         marcadores e seus itens
    POST/DELETE /bookmarks/{nome}, PUT /bookmarks/{nome}/add|remove

A especificação completa fica em <url>/swagger.json.
"""

import codecs
import hashlib
import json
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

import requests

from iped_mcp.config import WEBAPI_MAIN_CLASS, CaseConfig, find_java


class IPEDAPIError(RuntimeError):
    """Erro devolvido pela API do IPED (ou falha ao iniciá-la)."""


def _q(segment) -> str:
    return quote(str(segment), safe="")


def _error_message(resp: requests.Response) -> str:
    text = resp.text or ""
    # Grizzly devolve páginas HTML; aproveitar só o título/corpo
    if "<html" in text.lower():
        body = re.sub(r"<style.*?</style>|<title>.*?</title>|<[^>]+>", " ", text, flags=re.S)
        text = re.sub(r"\s+", " ", body.replace("Grizzly 2.4.4", "")).strip()
    return f"HTTP {resp.status_code}: {text[:300]}"


class IPEDWebAPI:
    """Cliente HTTP da API REST do IPED."""

    def __init__(self, base_url: str, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            resp = self.session.request(method, f"{self.base_url}/{path}",
                                        timeout=kwargs.pop("timeout", self.timeout), **kwargs)
        except requests.RequestException as e:
            raise IPEDAPIError(f"API do IPED inacessível em {self.base_url}: {e}") from e
        if resp.status_code >= 400:
            raise IPEDAPIError(_error_message(resp))
        return resp

    def _get_json(self, path: str, **params):
        return self._request("GET", path, params={k: v for k, v in params.items() if v is not None}).json()

    def is_alive(self) -> bool:
        try:
            return self.session.get(f"{self.base_url}/sources", timeout=5).status_code == 200
        except requests.RequestException:
            return False

    # ---------------------------------------------------------------- fontes

    def list_sources(self) -> list[dict]:
        return self._get_json("sources")["data"]

    def get_source(self, source_id: str) -> dict:
        return self._get_json(f"sources/{_q(source_id)}")

    def add_source(self, source_id: str, path: str) -> None:
        self._request("POST", "sources", json={"id": source_id, "path": path})

    def list_categories(self) -> list[str]:
        return self._get_json("categories")["data"]

    # ---------------------------------------------------------------- busca

    def search(self, query: str, source_id: Optional[str] = None) -> list[tuple[str, int]]:
        """Executar uma consulta (sintaxe de busca do IPED/Lucene). Retorna (fonte, id)."""
        try:
            data = self._get_json("search", q=query, sourceID=source_id)["data"]
        except IPEDAPIError as e:
            raise IPEDAPIError(f"Falha na busca {query!r} (sintaxe inválida ou fonte inexistente?) — {e}") from e
        return [(group["source"], doc_id) for group in data for doc_id in group["ids"]]

    # ---------------------------------------------------------------- documentos

    def get_document(self, source_id: str, doc_id: int) -> dict:
        try:
            return self._get_json(f"sources/{_q(source_id)}/docs/{int(doc_id)}")
        except IPEDAPIError as e:
            raise IPEDAPIError(f"Item {doc_id} não encontrado na fonte {source_id!r} — {e}") from e

    def get_text(self, source_id: str, doc_id: int, offset: int = 0, max_chars: int = 20000) -> dict:
        """Texto extraído, lido em streaming até offset+max_chars caracteres."""
        resp = self._request("GET", f"sources/{_q(source_id)}/docs/{int(doc_id)}/text", stream=True)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buf, total, truncated = [], 0, False
        try:
            for chunk in resp.iter_content(64 * 1024):
                piece = decoder.decode(chunk)
                buf.append(piece)
                total += len(piece)
                if total > offset + max_chars:
                    truncated = True
                    break
            else:
                buf.append(decoder.decode(b"", final=True))
        finally:
            resp.close()
        text = "".join(buf)
        return {"text": text[offset:offset + max_chars], "offset": offset, "truncated": truncated}

    def save_content(self, source_id: str, doc_id: int, dest: Path, thumb: bool = False) -> dict:
        """Gravar o conteúdo bruto (ou a miniatura) em dest, calculando MD5/SHA-256."""
        kind = "thumb" if thumb else "content"
        resp = self._request("GET", f"sources/{_q(source_id)}/docs/{int(doc_id)}/{kind}", stream=True)
        md5, sha256, size = hashlib.md5(), hashlib.sha256(), 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(1024 * 1024):
                    f.write(chunk)
                    md5.update(chunk)
                    sha256.update(chunk)
                    size += len(chunk)
        finally:
            resp.close()
        return {"path": str(dest), "size": size, "md5": md5.hexdigest(), "sha256": sha256.hexdigest()}

    # ---------------------------------------------------------------- bookmarks

    def list_bookmarks(self) -> list[str]:
        return self._get_json("bookmarks")["data"]

    def get_bookmark_docs(self, name: str) -> list[tuple[str, int]]:
        data = self._get_json(f"bookmarks/{_q(name)}")["data"]
        return [(group["source"], doc_id) for group in data for doc_id in group["ids"]]

    def create_bookmark(self, name: str) -> None:
        self._request("POST", f"bookmarks/{_q(name)}")

    def delete_bookmark(self, name: str) -> None:
        self._request("DELETE", f"bookmarks/{_q(name)}")

    def add_to_bookmark(self, name: str, docs: Iterable[tuple[str, int]]) -> None:
        self._request("PUT", f"bookmarks/{_q(name)}/add",
                      json=[{"source": s, "id": int(i)} for s, i in docs])

    def remove_from_bookmark(self, name: str, docs: Iterable[tuple[str, int]]) -> None:
        self._request("PUT", f"bookmarks/{_q(name)}/remove",
                      json=[{"source": s, "id": int(i)} for s, i in docs])


def _free_port(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


class WebAPIServer:
    """Processo Java da API REST do IPED servindo um ou mais casos."""

    def __init__(self, cases: list[CaseConfig], host: str = "127.0.0.1", port: int = 0,
                 java: Optional[str] = None, java_opts: Optional[list[str]] = None,
                 startup_timeout: float = 600):
        if not cases:
            raise ValueError("Nenhum caso informado")
        self.cases = cases
        self.host = host
        self.port = port
        self.java = java or find_java(cases[0].module_dir)
        self.java_opts = java_opts or []
        self.startup_timeout = startup_timeout
        self.process: Optional[subprocess.Popen] = None
        self.workdir = Path(tempfile.mkdtemp(prefix="iped-mcp-"))
        self.log_path = self.workdir / "webapi.log"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def command(self) -> list[str]:
        sources_file = self.workdir / "sources.json"
        sources_file.write_text(json.dumps([c.to_source() for c in self.cases]), encoding="utf-8")
        # O próprio Java expande "lib/*" no classpath
        classpath = str(self.cases[0].lib_dir / "*")
        return [self.java, *self.java_opts, "-cp", classpath, WEBAPI_MAIN_CLASS,
                f"--sources={sources_file}", f"--host={self.host}", f"--port={self.port}"]

    def log_tail(self, lines: int = 30) -> str:
        try:
            return "\n".join(self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def start(self) -> IPEDWebAPI:
        if not self.java:
            raise IPEDAPIError("Java não encontrado (nem iped/jre, nem JAVA_HOME, nem no PATH).")
        if not self.port:
            self.port = _free_port(self.host)
        log = open(self.log_path, "wb")
        self.process = subprocess.Popen(
            self.command(), cwd=str(self.cases[0].module_dir),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        )
        log.close()
        api = IPEDWebAPI(self.url)
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise IPEDAPIError(
                    f"A API do IPED terminou ao iniciar (código {self.process.returncode}).\n"
                    f"{self.log_tail()}"
                )
            if api.is_alive():
                return api
            time.sleep(0.5)
        self.stop()
        raise IPEDAPIError(f"A API do IPED não respondeu em {self.startup_timeout:.0f}s.\n{self.log_tail()}")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
