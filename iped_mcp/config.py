"""
Configuração do IPED MCP: localização do caso, das bibliotecas do IPED e do Java.

Um caso IPED processado tem a estrutura:
    <caso>/
        iped/
            index/      índice Lucene (metadados, texto, categorias, bookmarks)
            lib/        jars do IPED (inclui a API REST iped.engine.webapi.Main)
            jre/        Java embarcado (Windows)
            conf/
        IPED-SearchApp.exe / iped-search-app.jar
"""

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

WEBAPI_MAIN_CLASS = "iped.engine.webapi.Main"


def find_module_dir(path: Path) -> Optional[Path]:
    """Achar a pasta 'iped/' do caso a partir da raiz do caso ou da própria pasta 'iped/'."""
    for candidate in (path / "iped", path):
        if (candidate / "index").is_dir() and (candidate / "lib").is_dir():
            return candidate
    return None


def find_java(module_dir: Optional[Path]) -> Optional[str]:
    """Java para rodar a API: o JRE embarcado no caso, depois JAVA_HOME, depois o PATH."""
    exe = "java.exe" if sys.platform == "win32" else "java"
    candidates = []
    if module_dir:
        candidates.append(module_dir / "jre" / "bin" / exe)
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]) / "bin" / exe)
    for c in candidates:
        if c.is_file():
            return str(c)
    return shutil.which("java")


class CaseConfig:
    """Um caso IPED (fonte) servido pela API."""

    def __init__(self, case_path: str, source_id: Optional[str] = None):
        self.input_path = Path(case_path)
        self.module_dir = find_module_dir(self.input_path)
        if self.module_dir is None:
            raise ValueError(
                f"Não é um caso IPED (faltam iped/index e iped/lib): {self.input_path}"
            )
        # A API do IPED espera a raiz do caso (a pasta que contém 'iped/')
        self.case_root = self.module_dir.parent
        self.source_id = source_id or self.case_root.name
        self.lib_dir = self.module_dir / "lib"
        self.index_dir = self.module_dir / "index"

    def to_source(self) -> dict:
        return {"id": self.source_id, "path": str(self.case_root)}

    def __repr__(self):
        return f"CaseConfig({self.source_id!r}, {str(self.case_root)!r})"
