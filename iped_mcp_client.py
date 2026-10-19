"""
Cliente MCP para o servidor iped-mcp.

Comunica com o servidor MCP via subprocesso (stdin/stdout, JSON-RPC 2.0).
Use este script standalone ou importe como biblioteca.

Uso standalone:
    python iped_mcp_client.py --case-path /caminho/do/caso list-tools
    python iped_mcp_client.py --case-path /caminho/do/caso call search '{"query": "pix", "limit": 10}'

Uso como biblioteca:
    from iped_mcp_client import IpedMCPClient

    with IpedMCPClient("/caminho/do/caso") as client:
        print(client.call_tool("get_stats", {}))
"""

import json
import subprocess
import sys
import argparse
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent


class MCPStdioClient:
    """Cliente MCP genérico para um servidor stdio."""

    def __init__(self, server_args: list[str]):
        """Inicializar cliente e conectar ao servidor.

        Args:
            server_args: Comando do servidor após 'uv run' (ex: ['iped-mcp', '--case-path', '/caso']).
        """
        self.server_args = server_args
        self.proc = None
        self._request_id = 0
        self._initialized = False
        self._server_info = None
        self._connect()

    def _next_id(self) -> int:
        """Gerar ID de requisição único."""
        self._request_id += 1
        return self._request_id

    def _write(self, message: dict):
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except BrokenPipeError:
            raise RuntimeError(f"Conexão com o servidor MCP perdida. stderr:\n{self._stderr_tail()}")

    def _stderr_tail(self) -> str:
        if self.proc and self.proc.poll() is not None and self.proc.stderr:
            return self.proc.stderr.read()[-2000:]
        return ""

    def _notify(self, method: str, params: dict = None):
        """Enviar notificação JSON-RPC (sem id e sem resposta)."""
        message = {"jsonrpc": "2.0", "method": method}
        if params:
            message["params"] = params
        self._write(message)

    def _send(self, method: str, params: dict = None) -> dict:
        """Enviar requisição JSON-RPC e ler a resposta correspondente.

        Args:
            method: Método JSON-RPC (ex: 'tools/list').
            params: Parâmetros do método.

        Returns:
            Dicionário com a resposta JSON-RPC.
        """
        request_id = self._next_id()
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})

        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"Servidor MCP fechou a conexão. stderr:\n{self._stderr_tail()}")
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                raise RuntimeError(f"Resposta inválida do servidor: {line[:200]}")
            # Ignorar notificações do servidor (logs, progresso)
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"Erro do servidor MCP: {response['error']['message']}")
            return response

    def _connect(self):
        """Iniciar o servidor e fazer o handshake MCP."""
        try:
            subprocess.run(["uv", "--version"], capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "uv não encontrado. Instale em https://github.com/astral-sh/uv ou "
                "use Python virtualenv direto."
            )

        self.proc = subprocess.Popen(
            ["uv", "run", "--quiet", "--directory", str(PROJECT_DIR), *self.server_args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        resp = self._send("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "iped-mcp-client", "version": "0.2.0"},
        })
        self._server_info = resp.get("result", {})
        self._notify("notifications/initialized")
        self._initialized = True

    def list_tools(self) -> list[dict]:
        """Listar todas as ferramentas disponíveis no servidor.

        Returns:
            Lista de dicionários com name, description e inputSchema de cada ferramenta.
        """
        resp = self._send("tools/list")
        return resp["result"]["tools"]

    def call_tool(self, name: str, arguments: dict = None) -> str:
        """Chamar uma ferramenta no servidor.

        Args:
            name: Nome da ferramenta (ex: 'search', 'get_stats').
            arguments: Dicionário com os parâmetros da ferramenta.

        Returns:
            Texto da resposta (JSON formatado).
        """
        resp = self._send("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

        # MCP retorna como array de content blocks
        content_blocks = resp["result"]["content"]
        if not content_blocks:
            return ""

        # Pega o primeiro bloco de texto
        for block in content_blocks:
            if block.get("type") == "text":
                return block["text"]

        return json.dumps(content_blocks, indent=2, ensure_ascii=False)

    def list_prompts(self) -> list[dict]:
        """Listar prompts disponíveis (se o servidor suportar)."""
        try:
            resp = self._send("prompts/list")
            return resp["result"]["prompts"]
        except RuntimeError:
            return []

    def get_prompt(self, name: str, arguments: dict = None) -> dict:
        """Buscar prompt (se o servidor suportar)."""
        try:
            resp = self._send("prompts/get", {"name": name, "arguments": arguments or {}})
            return resp["result"]
        except RuntimeError:
            return {}

    def list_resources(self) -> list[dict]:
        """Listar recursos disponíveis (se o servidor suportar)."""
        try:
            resp = self._send("resources/list")
            return resp["result"]["resources"]
        except RuntimeError:
            return []

    def read_resource(self, uri: str) -> str:
        """Ler um recurso pelo URI (se o servidor suportar)."""
        try:
            resp = self._send("resources/read", {"uri": uri})
            return json.dumps(resp["result"], indent=2, ensure_ascii=False)
        except RuntimeError:
            return ""

    def close(self):
        """Encerrar conexão com o servidor."""
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                if self.proc.poll() is None:
                    self.proc.kill()
            self._initialized = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self):
        return f"{type(self).__name__}({self.server_args!r})"


class IpedMCPClient(MCPStdioClient):
    """Cliente para o servidor iped-mcp (casos IPED)."""

    def __init__(self, case_path: str):
        self.case_path = case_path
        super().__init__(["iped-mcp", "--case-path", case_path])


# =====================================================================
# CLI standalone
# =====================================================================

def main():
    """CLI standalone."""
    parser = argparse.ArgumentParser(
        description="Cliente MCP para o servidor iped-mcp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  # Listar ferramentas disponíveis:
  python iped_mcp_client.py --case-path /caminho/caso list-tools

  # Chamar ferramenta:
  python iped_mcp_client.py --case-path /caminho/caso call search '{"query": "pix", "limit": 10}'

  # Obter estatísticas:
  python iped_mcp_client.py --case-path /caminho/caso call get_stats '{}'

  # Buscar por hash:
  python iped_mcp_client.py --case-path /caminho/caso call search_by_hash '{"hash_value": "abc123"}'
        """,
    )
    parser.add_argument(
        "--case-path",
        required=True,
        help="Caminho para o diretório do caso IPED",
    )
    parser.add_argument(
        "command",
        choices=["list-tools", "call", "list-prompts", "get-prompt", "list-resources", "read-resource"],
        help="Comando a executar",
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="Argumentos (nome da ferramenta + JSON, ou argumentos do prompt/recurso)",
    )

    args = parser.parse_args()

    def make_client() -> MCPStdioClient:
        return IpedMCPClient(args.case_path)

    if args.command == "list-tools":
        with make_client() as client:
            tools = client.list_tools()
            for tool in tools:
                print(f"\n--- {tool['name']} ---")
                print(f"Descrição: {tool.get('description', '')}")
                if "inputSchema" in tool:
                    print(f"Schema: {json.dumps(tool['inputSchema'], indent=2, ensure_ascii=False)}")

    elif args.command == "call":
        if len(args.args) < 2:
            print("Erro: forneça nome da ferramenta e JSON de argumentos.", file=sys.stderr)
            print("Ex: call search '{\"query\": \"pix\"}'", file=sys.stderr)
            sys.exit(1)

        tool_name = args.args[0]
        try:
            tool_args = json.loads(args.args[1])
        except json.JSONDecodeError as e:
            print(f"Erro no JSON de argumentos: {e}", file=sys.stderr)
            sys.exit(1)

        with make_client() as client:
            result = client.call_tool(tool_name, tool_args)
            print(result)

    elif args.command == "list-prompts":
        with make_client() as client:
            prompts = client.list_prompts()
            print(json.dumps(prompts, indent=2, ensure_ascii=False, default=str))

    elif args.command == "get-prompt":
        if len(args.args) < 2:
            print("Erro: forneça nome do prompt e JSON de argumentos.", file=sys.stderr)
            sys.exit(1)
        prompt_name = args.args[0]
        try:
            prompt_args = json.loads(args.args[1])
        except json.JSONDecodeError as e:
            print(f"Erro no JSON de argumentos: {e}", file=sys.stderr)
            sys.exit(1)
        with make_client() as client:
            result = client.get_prompt(prompt_name, prompt_args)
            print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

    elif args.command == "list-resources":
        with make_client() as client:
            resources = client.list_resources()
            print(json.dumps(resources, indent=2, ensure_ascii=False, default=str))

    elif args.command == "read-resource":
        if not args.args:
            print("Erro: forneça URI do recurso.", file=sys.stderr)
            sys.exit(1)
        with make_client() as client:
            result = client.read_resource(args.args[0])
            print(result)


if __name__ == "__main__":
    main()
