# IPED MCP

[Português](README.md) | **English**

MCP (Model Context Protocol) server for working with **processed IPED cases** in natural language from
Claude (Code or Desktop), Hermes Agent or any other MCP client. It uses IPED's standard REST API and
works with any kind of evidence (disk images, UFDR, folders).

To read Cellebrite UFDR reports straight from the `.ufdr` file, without IPED, use the separate
`ufdr-mcp` project.

---

## 1. Installation

Requirements:

- Python 3.11+ and [`uv`](https://github.com/astral-sh/uv)
- For `iped-mcp`: a case already processed by IPED (4.x). Java ships with the case (`iped/jre`);
  without it, `JAVA_HOME` or the `java` on the `PATH` is used (Java 11+).

```bash
cd D:/workspace/iped_mcp          # or /home/silvio/Documentos/workspace/git/iped_mcp
uv sync
```

Quick test (should list the tools and show the case statistics):

```bash
uv run python iped_mcp_client.py --case-path F:/IMAGENS_PROCESSADAS/iphone list-tools
uv run python iped_mcp_client.py --case-path F:/IMAGENS_PROCESSADAS/iphone call get_stats "{}"
```

---

## 2. Starting iped-mcp

```bash
uv run iped-mcp                      # no case: the case is chosen in the conversation (recommended)
uv run iped-mcp --case-path <case>   # opens a fixed case right away
```

The server starts with no case, and the case is loaded during the conversation through the `add_case`
tool (*"load the case F:/IMAGENS_PROCESSADAS/iphone"*). This way, the MCP client configuration is the
same for every case. To switch cases in the same session, `close_case` unloads the current one
(*"close the current case and open F:/IMAGENS_PROCESSADAS/other"*).

`<case>` is the **root of the processed case**, the folder that contains `iped/`
(e.g. `F:/IMAGENS_PROCESSADAS/iphone`). The `iped/` folder itself is also accepted.

The server talks over stdio. It is normally started by the MCP client (section 4), not by you.
When run manually, it just sits waiting for messages; that is expected.

### How it works

```
MCP client ──stdio──> iped-mcp ──HTTP (127.0.0.1)──> IPED REST API (Java) ──> case iped/index
                                                     iped.engine.webapi.Main   + original evidence
```

1. When the case is loaded (`add_case`, or the first call with `--case-path`), `iped-mcp` starts IPED's
   standard REST API (`iped.engine.webapi.Main`, shipped in `iped/lib` of every case). It runs with the
   case's own Java on a free local port. On the test case (1.4 million items), the API is ready in
   about 4 s.
2. Tools become HTTP queries to the API: index search, properties, text, content and bookmarks.
3. When the client closes, the API is shut down with it.

### Options

| Option | Description |
|--------|-------------|
| `--case-path <case>` | Optional: case to load at startup. Can be repeated for several cases |
| `--api-url <url>` | Use an IPED API that is already running (e.g. on another machine) instead of starting one |
| `--java-opts "-Xmx8g"` | JVM options for the API (memory for large cases) |
| `--allow-write` | Enables the tools that **change the case bookmarks** (off by default) |
| `--eager` | Starts the API at startup instead of on the first call |

Equivalent environment variables: `IPED_API_URL` and `IPED_JAVA_OPTS`.

### Using an IPED API that is already running (optional)

To share one API between several clients, or keep it on a server, start it manually.
The `sources.json` file lists the cases:

```json
[{"id": "iphone", "path": "F:/IMAGENS_PROCESSADAS/iphone"}]
```

```bash
cd F:/IMAGENS_PROCESSADAS/iphone/iped
jre/bin/java -Xmx8g -cp "lib/*" iped.engine.webapi.Main --sources=sources.json --host=127.0.0.1 --port=8080
```

Then point the MCP server at it: `uv run iped-mcp --api-url http://127.0.0.1:8080`.
The API documentation is at `http://127.0.0.1:8080/swagger.json`.

> The API has no authentication. Use `--host=127.0.0.1` or restrict network access.

---

## 3. Usage

Once the server is set up in a client (section 4), just talk to it. Start by telling it the case:

- *"Load the case F:/IMAGENS_PROCESSADAS/iphone"* → `add_case`
- *"Close the current case and open F:/IMAGENS_PROCESSADAS/other"* → `close_case` + `add_case`
- *"Also load F:/IMAGENS_PROCESSADAS/other"* → `add_case` (queries both cases together)

Then ask about the case:

- *"Summarize the case: how many items, how many deleted, which categories?"* → `get_stats`
- *"Search for 'pix' and show the first 20 results"* → `search`
- *"Which phone calls are there? Any deleted?"* → `search_by_category`
- *"Is this hash in the case? 0f260eb0bf64143726b85412582a0cfe"* → `search_by_hash`
- *"Show all the metadata of item 69759"* → `get_document`
- *"Read the text of item 222686"* → `get_document_text`
- *"Export item 232401 to D:/output and check the hash"* → `export_document`
- *"List the photos modified in April 2026"* → `search` with `type:jpg AND modified:[2026-04-01 TO 2026-04-30]`

### iped-mcp tools

Each item is identified by `(source_id, id)`. With a single case loaded, `source_id` can be omitted.
Listings return `total`/`returned` and are paged with `limit`/`offset`.

| Tool | Description |
|------|-------------|
| `get_case_info()` | Loaded cases, API URL, Java in use and log path |
| `list_sources()` / `add_case(path, source_id)` | List cases / load another case |
| `close_case(source_id)` | Unload a case (or all of them, without `source_id`) to open another |
| `list_categories()` | Case categories |
| `get_stats(refresh)` | Total items, deleted, carved and count per category |
| `search(query, source_id, limit, offset, details)` | Search with IPED syntax (see below) |
| `count(query, source_id)` | Only the count for a query |
| `search_by_category(category)` | Items in a category |
| `search_by_hash(hash_value)` | Search by MD5, SHA-1 or SHA-256 |
| `get_children(doc_id)` | Children of a folder or container (attachments, extracted items) |
| `get_document(doc_id, all_properties)` | Summary metadata or all properties (`ufed:*`, EXIF...) |
| `get_document_text(doc_id, offset, max_chars)` | Extracted text, read in chunks |
| `export_document(doc_id, output_dir, thumbnail)` | Exports the content (checked against the case MD5: `hash_ok`) or the thumbnail |
| `list_bookmarks()` / `get_bookmark_items(bookmark)` | Bookmarks and their items |
| `create_bookmark` / `add_to_bookmark` / `remove_from_bookmark` | Change bookmarks (require `--allow-write`) |

### Search syntax (IPED/Lucene)

| Query | Meaning |
|-------|---------|
| `pix` | word in the text or metadata |
| `"conta corrente"` | exact phrase |
| `category:"Phone Calls"` | by category (see `list_categories`) |
| `type:jpg` / `name:WhatsApp*` | by extension / name with wildcard |
| `deleted:true` / `carved:true` | deleted / carved |
| `size:[1000000 TO *]` | size range in bytes |
| `modified:[2026-04-01 TO 2026-04-30]` | date range (`created`, `accessed` and `modified`) |
| `parentId:2008` | children of an item |
| `type:jpg AND NOT deleted:true` | combinations with `AND` / `OR` / `NOT` |
| `*` | everything |

In field names, escape `:` and `-` with `\`: `ufed\:Source:WhatsApp`, `sha\-256:<hash>`.

### Command-line client

```bash
uv run python iped_mcp_client.py --case-path <case> list-tools
uv run python iped_mcp_client.py --case-path <case> call get_stats "{}"
uv run python iped_mcp_client.py --case-path <case> call search '{"query": "deleted:true", "limit": 5}'
```

> In PowerShell, JSON inside single quotes needs its inner quotes escaped
> (`'{\"query\": \"pix\"}'`). Prefer Git Bash, or use the Python library.

---

## 4. MCP client integration

There is **a single configuration for every case and every client**. The server starts with no case,
and the case is chosen in the conversation (`add_case` / `close_case`, section 3). The command is the
same in every client:

```
uv run --directory D:/workspace/iped_mcp iped-mcp
```

The only thing that changes between clients is where that command is registered. In the examples below,
replace `D:/workspace/iped_mcp` with the project folder (on Linux,
`/home/silvio/Documentos/workspace/git/iped_mcp`). The options from section 2 (`--java-opts`,
`--allow-write`...) go at the end of the command, or in `args`.

### Claude Code

```bash
claude mcp add -s user iped -- uv run --directory D:/workspace/iped_mcp iped-mcp

# Check
claude mcp list
```

- `-s user` makes the server available in every project, configured only once. Use `-s project` to
  write it to a `.mcp.json` in the current folder that can be committed.
- Inside Claude Code, `/mcp` shows the server status and its tools.

Equivalent `.mcp.json` file (at the root of the project where you run `claude`), the same as this
repository's `mcp_config.json`:

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

Edit `claude_desktop_config.json`:

- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

The content is the same `mcpServers` block as the `.mcp.json` above (or `mcp_config.json`, adjusting the
project path). Restart Claude Desktop after saving. If `uv` is not on the app's `PATH`, use the full path
in `"command"` (e.g. `"C:/Users/Silvio/.local/bin/uv.exe"`).

### Hermes Agent

Hermes Agent loads the MCP servers declared in its configuration file
(`~/.hermes/config.yaml`, `mcp_servers` section):

```yaml
mcp_servers:
  iped:
    command: uv
    args: ["run", "--directory", "D:/workspace/iped_mcp", "iped-mcp"]
    env:
      IPED_JAVA_OPTS: "-Xmx8g"
```

Restart Hermes. The tools show up in the agent's toolbox; to check, ask
*"list the available IPED tools"*. If your Hermes version uses a JSON file for MCP servers, the format is
the same `mcpServers` block as `mcp_config.json`. More details, and usage through `execute_code`, are in
[`INTEGRACAO_HERMES.md`](INTEGRACAO_HERMES.md) (in Portuguese).

Without native MCP, the agent can use the client as a Python library:

```python
from iped_mcp_client import IpedMCPClient

with IpedMCPClient("F:/IMAGENS_PROCESSADAS/iphone") as client:
    print(client.call_tool("get_stats", {}))
    print(client.call_tool("search", {"query": 'category:"Phone Calls"', "limit": 10}))
```

### Other clients

Cursor, VS Code and most MCP clients use the same `mcpServers` block as `mcp_config.json`; only the
location of each one's configuration file changes. Any stdio MCP client works with
`uv run --directory <project> iped-mcp`.

### Fixed case (optional)

For an agent that always opens the same case, add `--case-path <case>` to the command
(or `"--case-path", "<case>"` to `args`). You can still switch cases through the conversation.

### Tips

- **Slower loading:** the IPED API is started on `add_case` (or on the first call). For very large
  cases, use `--eager` or increase the client's tool timeout.
- **Original evidence:** to export content and extract text, the evidence (the `.ufdr`, the image...)
  must be reachable at the path recorded in the case. Metadata and search work even without it.
- **Protected bookmarks:** by default the server does not change anything in the case. Enable
  `--allow-write` only if you want the agent to create or edit bookmarks.
- **API log:** its path is shown by `get_case_info` (`webapi_log`). Useful if the API does not start.

---

## 5. As a Python library

```python
from iped_mcp import CaseConfig, WebAPIServer

# Starts the case's own REST API and queries it
server = WebAPIServer([CaseConfig("F:/IMAGENS_PROCESSADAS/iphone")])
api = server.start()
ids = api.search('category:"Phone Calls"')        # [(source, id), ...]
doc = api.get_document(*ids[0])                    # item properties
print(api.get_text(*ids[0])["text"])
server.stop()
```

---

## 6. Layout

```
iped_mcp/
├── pyproject.toml      # uv project (iped-mcp entry point)
├── iped_mcp/           # MCP server
│   ├── main.py         # MCP server (tools)
│   ├── config.py       # Locates the case, iped/lib and Java
│   └── webapi.py       # IPED REST API client and launcher
├── iped_mcp_client.py  # CLI client / library
├── mcp_config.json     # Example configuration for MCP clients
└── tests/
```

## 7. Tests

```bash
uv run pytest tests/ -v
# integration with a real IPED case (starts the case's API)
IPED_TEST_CASE=F:/IMAGENS_PROCESSADAS/iphone uv run pytest tests/ -v -k real_case
```

The unit tests use a mock IPED API and need neither Java nor a case.
