# Integração IPED MCP com Hermes Agent

## Visão Geral

Este documento explica como integrar o servidor MCP do IPED com o Hermes Agent
para que o agente possa explorar dados forenses IPED de forma natural via linguagem.

---

## 1. O que foi criado

```
/home/silvio/Documentos/workspace/git/iped_mcp/
├── iped_mcp/                  # Pacote Python (servidor MCP)
│   ├── main.py                # Servidor MCP (MCPServer v2)
│   ├── webapi.py              # Cliente/inicializador da API REST padrão do IPED
│   └── config.py              # Localização do caso, de iped/lib e do Java
├── iped_mcp_client.py         # Cliente standalone (CLI + biblioteca)
├── mcp_config.json            # Configuração MCP para Hermes
├── pyproject.toml             # Projeto uv
└── README.md                  # Documentação completa
```

---

## 2. Como integrar ao Hermes Agent

Existem **duas formas** de integrar:

### Opção A: MCP Server nativo (recomendado)

O Hermes Agent suporta MCP servers nativamente.

**Passo 1:** Adicionar o servidor à configuração do Hermes

Na seção `mcp_servers` do `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  iped:
    command: uv
    args: ["run", "--directory", "/home/silvio/Documentos/workspace/git/iped_mcp", "iped-mcp"]
    env:
      IPED_JAVA_OPTS: "-Xmx8g"
```

O caso não fica no config: ele é informado na conversa (seção 5). No Windows, use caminhos como
`D:/workspace/iped_mcp`.

Se a sua versão do Hermes usar um arquivo JSON de servidores MCP, o conteúdo equivalente é o do
`mcp_config.json`:
```json
{
  "mcpServers": {
    "iped": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/home/silvio/Documentos/workspace/git/iped_mcp",
        "iped-mcp"
      ]
    }
  }
}
```

**Passo 2:** Reiniciar o Hermes Agent

Após salvar, o Hermes detectará automaticamente o novo MCP server
e as ferramentas ficarão disponíveis no toolbox do agente.

**Passo 3:** Verificar que as ferramentas estão disponíveis

Pergunte ao agente: *"Liste as ferramentas IPED disponíveis"*. Ele deve citar `get_stats`,
`search`, `get_document` etc.

---

### Opção B: Como biblioteca Python (via execute_code)

Se preferir não usar MCP nativo, você pode usar o cliente como biblioteca
dentro de scripts Python executados pelo Hermes.

**Exemplo de uso no Hermes via `execute_code`:**

```python
# Executar no terminal do Hermes:
# cd /home/silvio/Documentos/workspace/git/iped_mcp && uv run python iped_mcp_client.py --case-path /caminho/caso call search '{"query": "pix", "limit": 10}'

# OU via execute_code do Hermes:
from iped_mcp_client import IpedMCPClient

with IpedMCPClient("/caminho/do/caso") as client:
    # Listar ferramentas disponíveis
    tools = client.list_tools()
    for t in tools:
        print(f"{t['name']}: {t['description']}")

    # Chamar uma ferramenta
    result = client.call_tool("get_stats", {})
    print(result)

    # Buscar itens (sintaxe de consulta do IPED)
    result = client.call_tool("search", {"query": "deleted:true", "limit": 5})
    print(result)
```

---

## 3. Fluxo de interação (como o agente vai usar)

Quando o usuário diz algo como:

> "Me mostre os arquivos do caso IPED"
> "Quantos arquivos foram deletados?"
> "Busque por hash abc123..."

O agente Hermes irá:

1. **Entender a intenção** → Mapear para a ferramenta MCP correta
2. **Construir os parâmetros** → Baseado no inputSchema da ferramenta
3. **Chamar a ferramenta** → Via `tools/call` (MCP) ou `client.call_tool()` (Python)
4. **Processar a resposta** → Exibir ao usuário em linguagem natural

---

## 4. Ferramentas disponíveis via MCP

| Ferramenta | Uso típico no Hermes |
|---|---|
| `add_case(path)` | "Carregue o caso F:/IMAGENS_PROCESSADAS/iphone" |
| `close_case(source_id)` | "Feche o caso atual" / "Descarregue o caso iphone" |
| `get_stats()` | "Me dê um resumo do caso" |
| `search(query, ...)` | "Busque por 'pix'", "Mostre os arquivos apagados" (`deleted:true`) |
| `search_by_category(category)` | "Quais chamadas telefônicas existem?" |
| `search_by_hash(hash)` | "Verifique se este hash existe" |
| `get_document(doc_id)` / `get_document_text(doc_id)` | "Mostre os metadados / o texto do item 123" |
| `export_document(doc_id, output_dir)` | "Exporte esse arquivo para /tmp/saida" |

Lista completa no README.

---

## 5. Escolhendo o caso

O servidor inicia sem caso. Informe o caminho na conversa, por exemplo:

> "Carregue o caso F:/IMAGENS_PROCESSADAS/iphone e me dê um resumo"

O agente chama `add_case(path="F:/IMAGENS_PROCESSADAS/iphone")`. O caminho é a raiz do caso processado
(a pasta que contém `iped/`); a própria pasta `iped/` também é aceita. Nesse momento o servidor sobe a
API REST padrão do IPED (`iped/lib`) com o Java do caso (`iped/jre`, ou `JAVA_HOME`/`PATH`). Em casos
grandes, a primeira resposta demora alguns segundos.

- Se você pedir algo sem ter informado o caso, `get_case_info` avisa que nenhum caso foi carregado e o
  agente pergunta o caminho.
- Para consultar mais de um caso na mesma sessão, peça para carregar o outro (`add_case` de novo).
  Os itens passam a ser identificados por `source_id` (por padrão, o nome da pasta do caso).
- Para trocar de caso, peça *"feche o caso atual e abra F:/IMAGENS_PROCESSADAS/outro"*. O agente
  chama `close_case()` (descarrega todos) e depois `add_case`. Com vários casos carregados,
  `close_case(source_id="iphone")` descarrega só aquele. Também funciona abrir uma nova sessão do Hermes,
  que reinicia o servidor MCP sem caso.
- Ao descarregar, a API do IPED (Java) é encerrada e libera a memória; se restarem casos, ela sobe de
  novo com eles na próxima chamada. Não vale para `--api-url`, onde a API é externa.

Alternativas fixas no config: acrescente `"--case-path", "<caso>"` aos `args` para sempre abrir o
mesmo caso, ou `"--api-url", "http://host:porta"` para usar uma API do IPED já em execução.

---

## 6. Troubleshooting

### Servidor não inicia
```bash
# Testar manualmente
cd /home/silvio/Documentos/workspace/git/iped_mcp
uv run iped-mcp
# Deve ficar em espera (stdin/stdout)

# Testar o carregamento de um caso
uv run python iped_mcp_client.py --case-path "/seu/caso" call get_case_info "{}"

# Se der erro de módulo
uv pip install -e .
```

### Ferramentas não aparecem
1. Verificar se o `mcp_config.json` está no lugar certo
2. Reiniciar o Hermes Agent
3. Verificar logs do MCP connection

### Erro de subprocesso no cliente
```
RuntimeError: uv não encontrado
```
Instale o uv: https://github.com/astral-sh/uv

---

## 7. Exemplo completo de interação

Usuário: "Me mostra as estatísticas do caso IPED"

Agente executa (via MCP nativo):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "get_stats",
    "arguments": {}
  }
}
```

Resposta (trecho, caso real de teste):
```json
{
  "result": {
    "content": [{
      "type": "text",
      "text": "{\"total_items\": 1401735, \"deleted\": 28, \"carved\": 16915, \"by_category\": [...]}"
    }]
  }
}
```

Agente responde ao usuário: "O caso tem 1.401.735 itens, 28 apagados e 16.915 recuperados (carved)..."

---

## 8. Arquivos gerados

| Arquivo | Descrição |
|---|---|
| `iped_mcp_client.py` | Cliente standalone (CLI + biblioteca) |
| `mcp_config.json` | Configuração MCP para Hermes |
| `iped_mcp/` | Pacote servidor MCP (já criado anteriormente) |
