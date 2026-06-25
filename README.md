# ticker-mcp

MCP server for real-time stock quotes and market data. No API key required.

## Tools

| Tool | Description |
|------|-------------|
| `get_quote` | Real-time quote for one or more stocks |
| `get_market_summary` | Major market indices overview (Shanghai, Shenzhen, CSI 300, etc.) |

## Setup

```bash
# Install
pip install "mcp[cli]"

# Run (stdio mode — for Claude Desktop, Cursor, etc.)
python server.py

# Run (SSE mode — for remote access)
python server.py --transport sse --port 8080
```

## Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ticker": {
      "command": "python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Data Source

Data is sourced from public financial data feeds. No API key or authentication required.

## License

MIT
