# toolkit-mcp

MCP server for developer utilities. Clean architecture, extensible design.

## Tools

| Tool | Description |
|------|-------------|
| `validate_json` | Validate and pretty-print JSON |
| `json_to_schema` | Infer JSON Schema from example data |
| `base64` | Encode or decode base64 |
| `timestamp` | Convert unix timestamp to readable date |
| `hash` | Hash a string (md5, sha1, sha256, sha512) |

## Architecture

```
server.py
├── ToolRegistry          ← Reusable framework for tool registration
│   ├── tool() decorator  ← Auto-generates JSON Schema from type hints
│   ├── list_tools()      ← Full MCP tool listing
│   └── call_tool()       ← Dispatch with error handling
├── Tools                 ← Utility implementations (self-contained)
└── Server                ← MCP protocol binding (stdio / SSE)
```

The `ToolRegistry` pattern separates tool definition from protocol transport. To add a new tool:

```python
@registry.tool("my_tool", "Description of what it does")
def my_tool(param1: str, param2: int = 42) -> dict:
    \"\"\"Docstring becomes tool description.\"\"\"
    return {"result": param1 * param2}
```

That's it. The schema is inferred from type hints automatically.

## Setup

```bash
pip install "mcp[cli]"
python server.py                     # stdio mode (for Claude Desktop)
python server.py --transport sse     # HTTP mode (for remote access)
```

## Requirements

- Python 3.11+
- MCP SDK (`mcp[cli]`)

## License

MIT
