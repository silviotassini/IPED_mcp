# IPED MCP

**Português** | [English](README.en.md)

Servidor MCP (Model Context Protocol) para usar casos **IPED processados** em linguagem natural a partir
do Claude (Code ou Desktop), do Hermes Agent ou de qualquer cliente MCP. Usa a API REST padrão do IPED e
funciona com qualquer evidência (imagem de disco, UFDR, pastas).

Para ler relatórios UFDR da Cellebrite direto do `.ufdr`, sem IPED, use o projeto separado `ufdr-mcp`.

---

## 1. Instalação

Requisitos:

- Python 3.11+ e [`uv`](https://github.com/astral-sh/uv)
- Para o `iped-mcp`: um caso já processado pelo IPED (4.x). O Java vem junto com o caso
  (`iped/jre`); sem ele, é usado o `JAVA_HOME` ou o `java` do `PATH` (Java 11+).

```bash
cd D:/workspace/iped_mcp          # ou /home/silvio/Documentos/workspace/git/iped_mcp
uv sync
```

Teste rápido (deve listar as ferramentas e mostrar as estatísticas do caso):

```bash
uv run python iped_mcp_client.py --case-path F:/IMAGENS_PROCESSADAS/iphone list-tools
uv run python iped_mcp_client.py --case-path F:/IMAGENS_PROCESSADAS/iphone call get_stats "{}"
```

---

## 2. Iniciando o iped-mcp

```bash
uv run iped-mcp                      # sem caso: o caso é escolhido na conversa (recomendado)
uv run iped-mcp --case-path <caso>   # já abre num caso fixo
```

O servidor inicia sem caso, e o caso é carregado durante a conversa pela ferramenta `add_case`
(*"carregue o caso F:/IMAGENS_PROCESSADAS/iphone"*). Assim, a configuração do cliente MCP é a mesma para
qualquer caso. Para trocar de caso na mesma sessão, `close_case` descarrega o atual
(*"feche o caso atual e abra F:/IMAGENS_PROCESSADAS/outro"*).

`<caso>` é a **raiz do caso processado**, a pasta que contém `iped/` (ex.: `F:/IMAGENS_PROCESSADAS/iphone`).
A própria pasta `iped/` também é aceita.

O servidor conversa por stdio. Quem o inicia normalmente é o cliente MCP (seção 4), e não você.
Rodando manualmente, ele fica parado esperando mensagens; isso é o esperado.

### Como funciona

```
Cliente MCP ──stdio──> iped-mcp ──HTTP (127.0.0.1)──> API REST do IPED (Java) ──> iped/index do caso
                                                     iped.engine.webapi.Main     + evidência original
```

1. Ao carregar o caso (`add_case`, ou na primeira chamada com `--case-path`), o `iped-mcp` inicia
   a API REST padrão do IPED (`iped.engine.webapi.Main`, que vem em `iped/lib` de todo caso).
   Ela roda com o Java do próprio caso numa porta local livre. No caso de teste (1,4 milhão de itens), a API fica pronta em cerca de 4 s.
2. As ferramentas viram consultas HTTP à API: busca no índice, propriedades, texto, conteúdo e marcadores.
3. Ao fechar o cliente, a API é encerrada junto.

### Opções

| Opção | Descrição |
|-------|-----------|
| `--case-path <caso>` | Opcional: caso a carregar já na inicialização. Pode repetir para vários casos |
| `--api-url <url>` | Usar uma API do IPED já em execução (ex.: em outra máquina) em vez de iniciar uma |
| `--java-opts "-Xmx8g"` | Opções da JVM da API (memória em casos grandes) |
| `--allow-write` | Libera as ferramentas que **alteram marcadores** do caso (desligado por padrão) |
| `--eager` | Inicia a API já na inicialização, e não na primeira chamada |

Variáveis de ambiente equivalentes: `IPED_API_URL` e `IPED_JAVA_OPTS`.

### Usar uma API do IPED já em execução (opcional)

Para compartilhar uma API entre vários clientes, ou deixá-la num servidor, inicie-a manualmente.
O arquivo `sources.json` lista os casos:

```json
[{"id": "iphone", "path": "F:/IMAGENS_PROCESSADAS/iphone"}]
```

```bash
cd F:/IMAGENS_PROCESSADAS/iphone/iped
jre/bin/java -Xmx8g -cp "lib/*" iped.engine.webapi.Main --sources=sources.json --host=127.0.0.1 --port=8080
```

Depois, aponte o MCP para ela: `uv run iped-mcp --api-url http://127.0.0.1:8080`.
A documentação da API fica em `http://127.0.0.1:8080/swagger.json`.

> A API não tem autenticação. Use `--host=127.0.0.1` ou restrinja o acesso pela rede.

---

## 3. Usando

Depois de integrar a um cliente (seção 4), basta conversar. Comece indicando o caso:

- *"Carregue o caso F:/IMAGENS_PROCESSADAS/iphone"* → `add_case`
- *"Feche o caso atual e abra F:/IMAGENS_PROCESSADAS/outro"* → `close_case` + `add_case`
- *"Carregue também F:/IMAGENS_PROCESSADAS/outro"* → `add_case` (consulta os dois casos juntos)

Depois, pergunte sobre o caso:

- *"Resuma o caso: quantos itens, quantos apagados, quais categorias?"* → `get_stats`
- *"Busque 'pix' e mostre os 20 primeiros resultados"* → `search`
- *"Quais chamadas telefônicas existem? Alguma apagada?"* → `search_by_category`
- *"Esse hash está no caso? 0f260eb0bf64143726b85412582a0cfe"* → `search_by_hash`
- *"Mostre todos os metadados do item 69759"* → `get_document`
- *"Leia o texto do item 222686"* → `get_document_text`
- *"Exporte o item 232401 para D:/saida e confira o hash"* → `export_document`
- *"Liste as fotos modificadas em abril de 2026"* → `search` com `type:jpg AND modified:[2026-04-01 TO 2026-04-30]`

### Ferramentas do iped-mcp

Cada item é identificado por `(source_id, id)`. Com um só caso carregado, `source_id` pode ser omitido.
As listagens retornam `total`/`returned` e paginam com `limit`/`offset`.

| Ferramenta | Descrição |
|------------|-----------|
| `get_case_info()` | Casos carregados, URL da API, Java usado e caminho do log |
| `list_sources()` / `add_case(path, source_id)` | Listar casos / carregar outro caso |
| `close_case(source_id)` | Descarregar um caso (ou todos, sem `source_id`) para abrir outro |
| `list_categories()` | Categorias do caso |
| `get_stats(refresh)` | Total de itens, apagados, recuperados (carved) e contagem por categoria |
| `search(query, source_id, limit, offset, details)` | Busca com a sintaxe do IPED (veja abaixo) |
| `count(query, source_id)` | Só a contagem de uma consulta |
| `search_by_category(category)` | Itens de uma categoria |
| `search_by_hash(hash_value)` | Busca por MD5, SHA-1 ou SHA-256 |
| `get_children(doc_id)` | Filhos de uma pasta ou container (anexos, itens extraídos) |
| `get_document(doc_id, all_properties)` | Metadados resumidos ou todas as propriedades (`ufed:*`, EXIF...) |
| `get_document_text(doc_id, offset, max_chars)` | Texto extraído, lido em partes |
| `export_document(doc_id, output_dir, thumbnail)` | Exporta o conteúdo (confere com o MD5 do caso: `hash_ok`) ou a miniatura |
| `list_bookmarks()` / `get_bookmark_items(bookmark)` | Marcadores e seus itens |
| `create_bookmark` / `add_to_bookmark` / `remove_from_bookmark` | Alteram marcadores (exigem `--allow-write`) |

### Sintaxe de busca (IPED/Lucene)

| Consulta | Significado |
|----------|-------------|
| `pix` | palavra no texto ou nos metadados |
| `"conta corrente"` | frase exata |
| `category:"Phone Calls"` | por categoria (veja `list_categories`) |
| `type:jpg` / `name:WhatsApp*` | por extensão / nome com curinga |
| `deleted:true` / `carved:true` | apagados / recuperados |
| `size:[1000000 TO *]` | faixa de tamanho em bytes |
| `modified:[2026-04-01 TO 2026-04-30]` | faixa de datas (`created`, `accessed` e `modified`) |
| `parentId:2008` | filhos de um item |
| `type:jpg AND NOT deleted:true` | combinações com `AND` / `OR` / `NOT` |
| `*` | tudo |

Em nomes de campo, escape `:` e `-` com `\`: `ufed\:Source:WhatsApp`, `sha\-256:<hash>`.

### Cliente de linha de comando

```bash
# IPED
uv run python iped_mcp_client.py --case-path <caso> list-tools
uv run python iped_mcp_client.py --case-path <caso> call get_stats "{}"
uv run python iped_mcp_client.py --case-path <caso> call search '{"query": "deleted:true", "limit": 5}'
```

> No PowerShell, o JSON entre aspas simples precisa de aspas internas escapadas
> (`'{\"query\": \"pix\"}'`). Prefira o Git Bash, ou use a biblioteca Python.

---

## 4. Integração com clientes MCP

A configuração é **uma só, para qualquer caso e qualquer cliente**. O servidor inicia sem caso, e o caso
é escolhido na conversa (`add_case` / `close_case`, seção 3). Em todos os clientes, o comando é o mesmo:

```
uv run --directory D:/workspace/iped_mcp iped-mcp
```

O que muda de um cliente para outro é só onde esse comando fica registrado. Nos exemplos abaixo, troque
`D:/workspace/iped_mcp` pela pasta do projeto (no Linux, `/home/silvio/Documentos/workspace/git/iped_mcp`).
As opções da seção 2 (`--java-opts`, `--allow-write`...) vão no fim do comando, ou nos `args`.

### Claude Code

```bash
claude mcp add -s user iped -- uv run --directory D:/workspace/iped_mcp iped-mcp

# Conferir
claude mcp list
```

- `-s user` deixa o servidor disponível em todos os projetos, configurado uma vez só. Use `-s project`
  para gravar num `.mcp.json` versionável na pasta atual.
- Dentro do Claude Code, `/mcp` mostra o estado dos servidores e as ferramentas.

Arquivo `.mcp.json` equivalente (na raiz do projeto onde você roda o `claude`), o mesmo do
`mcp_config.json` deste repositório:

```json
{
  "mcpServers": {
    "iped": {
      "command": "uv",
      "args": ["run", "--directory", "D:/workspace/iped_mcp", "iped-mcp"]
    }
  }
}
```

### Claude Desktop

Edite o `claude_desktop_config.json`:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

O conteúdo é o mesmo bloco `mcpServers` do `.mcp.json` acima (ou do `mcp_config.json`, ajustando o
caminho do projeto). Reinicie o Claude Desktop depois de salvar. Se o `uv` não estiver no `PATH` do
aplicativo, use o caminho completo em `"command"` (ex.: `"C:/Users/Silvio/.local/bin/uv.exe"`).

### Hermes Agent

O Hermes Agent carrega servidores MCP declarados no arquivo de configuração dele
(`~/.hermes/config.yaml`, seção `mcp_servers`):

```yaml
mcp_servers:
  iped:
    command: uv
    args: ["run", "--directory", "D:/workspace/iped_mcp", "iped-mcp"]
    env:
      IPED_JAVA_OPTS: "-Xmx8g"
```

Reinicie o Hermes. As ferramentas aparecem no toolbox do agente; para conferir, peça
*"liste as ferramentas IPED disponíveis"*. Se a sua versão do Hermes usar um arquivo JSON de servidores
MCP, o formato é o mesmo `mcpServers` do `mcp_config.json`. Mais detalhes e o uso via
`execute_code` estão em [`INTEGRACAO_HERMES.md`](INTEGRACAO_HERMES.md).

Sem MCP nativo, o agente pode usar o cliente como biblioteca Python:

```python
from iped_mcp_client import IpedMCPClient

with IpedMCPClient("F:/IMAGENS_PROCESSADAS/iphone") as client:
    print(client.call_tool("get_stats", {}))
    print(client.call_tool("search", {"query": 'category:"Phone Calls"', "limit": 10}))
```

### Outros clientes

Cursor, VS Code e a maioria dos clientes MCP usam o mesmo bloco `mcpServers` do `mcp_config.json`;
muda só o caminho do arquivo de configuração de cada um. Qualquer cliente MCP por stdio funciona com
`uv run --directory <projeto> iped-mcp`.

### Caso fixo (opcional)

Para um agente que sempre abre no mesmo caso, acrescente `--case-path <caso>` ao comando
(ou `"--case-path", "<caso>"` aos `args`). Ainda assim, dá para trocar de caso pela conversa.

### Dicas

- **Carregamento mais lento:** a API do IPED é iniciada no `add_case` (ou na primeira chamada).
  Em casos muito grandes, use `--eager` ou aumente o timeout de ferramentas do cliente.
- **Evidência original:** para exportar conteúdo e extrair texto, a evidência (o `.ufdr`, a imagem...)
  precisa estar acessível no caminho gravado no caso. Os metadados e a busca funcionam mesmo sem ela.
- **Marcadores protegidos:** por padrão o servidor não altera nada no caso. Habilite `--allow-write`
  só se quiser que o agente crie ou edite marcadores.
- **Log da API:** o caminho aparece em `get_case_info` (`webapi_log`). É útil se a API não iniciar.

---

## 5. Como biblioteca Python

```python
from iped_mcp import CaseConfig, WebAPIServer

# IPED: sobe a API REST do próprio caso e consulta
server = WebAPIServer([CaseConfig("F:/IMAGENS_PROCESSADAS/iphone")])
api = server.start()
ids = api.search('category:"Phone Calls"')        # [(fonte, id), ...]
doc = api.get_document(*ids[0])                    # propriedades do item
print(api.get_text(*ids[0])["text"])
server.stop()
```

---

## 6. Estrutura

```
iped_mcp/
├── pyproject.toml      # Projeto uv (entry point iped-mcp)
├── iped_mcp/           # Servidor MCP
│   ├── main.py         # Servidor MCP (ferramentas)
│   ├── config.py       # Localização do caso, de iped/lib e do Java
│   └── webapi.py       # Cliente e inicializador da API REST do IPED
├── iped_mcp_client.py  # Cliente CLI/biblioteca
├── mcp_config.json     # Exemplo de configuração para clientes MCP
└── tests/
```

## 7. Testes

```bash
uv run pytest tests/ -v
# integração com um caso IPED real (sobe a API do caso)
IPED_TEST_CASE=F:/IMAGENS_PROCESSADAS/iphone uv run pytest tests/ -v -k real_case
```

Os testes unitários usam uma API IPED simulada e não precisam de Java nem de caso.
