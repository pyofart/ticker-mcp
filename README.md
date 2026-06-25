# toolkit-mcp

Meta-MCP & A2A server — tools for building, testing, and connecting AI agents.

## Tools

### Meta-MCP — Tools about MCP itself

| Tool | Description |
|------|-------------|
| `mcp_validate_config` | Validate MCP server config (Claude / Cursor `mcpServers`) |
| `mcp_inspect_schema` | Analyze a JSON Schema and explain its MCP tool mapping |
| `mcp_format_response` | Wrap data into proper MCP response format |

### A2A — Agent-to-Agent capability registry

| Tool | Description |
|------|-------------|
| `a2a_register` | Register an agent's capabilities |
| `a2a_discover` | Find agents by capability or keyword |
| `a2a_list_all` | List all registered agents |
| `a2a_submit_task` | Submit a task for processing |
| `a2a_get_task` | Check task status and get results |
| `a2a_list_tasks` | List all tasks, optionally filtered by status |

## Architecture

```
server.py
├── ToolRegistry          ← Decorator-based framework with auto schema inference
├── Meta-MCP Tools        ← MCP diagnostic, validation, formatting utilities
└── A2A Module            ← In-memory agent registry & task delegation system
```

Adding a new tool:

```python
@registry.tool("tool_name", "What it does")
def my_tool(param1: str, param2: int = 42) -> dict:
    """Docstring becomes tool description."""
    return {"result": param1 * param2}
```

Schema is auto-generated from type hints.

## Setup

```bash
pip install "mcp[cli]"
python server.py                     # stdio mode
python server.py --transport sse     # HTTP mode (port 8080)
```

## Claude Desktop

```json
{
  "mcpServers": {
    "toolkit": {
      "command": "python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Requirements

- Python 3.11+
- MCP SDK (`mcp[cli]`)

## License

MIT
