"""
ticker-mcp — MCP server for real-time stock market data.

Provides real-time stock quotes and market data via the Model Context Protocol.
No API key required. Data sourced from public financial data feeds.

Usage:
    # Install
    pip install "mcp[cli]"
    
    # Run stdio mode (for Claude Desktop / Cursor)
    python server.py
    
    # Run HTTP mode
    python server.py --transport sse --port 8080

Tools:
    - get_quote:       Real-time quote for one or more stocks
    - get_market_summary: Major market indices overview
    - get_sector_data:  Sector performance overview
"""

from typing import Any
import urllib.request
import urllib.parse
import json
import codecs

# ─── MCP SDK ──────────────────────────────────────────────
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    import mcp.server.models as models
except ImportError:
    raise ImportError(
        "MCP SDK required. Install with: pip install 'mcp[cli]'"
    )

# ══════════════════════════════════════════════════════════
#  DATA LAYER — Public financial data API
# ══════════════════════════════════════════════════════════

API_BASE = "https://qt.gtimg.cn"

# Known market indices codes
INDICES = {
    "sh000001": "Shanghai Composite",
    "sz399001": "Shenzhen Component",
    "sz399006": "ChiNext",
    "sh000688": "STAR 50",
    "sh000300": "CSI 300",
    "sh000016": "SSE 50",
    "sh000905": "CSI 500",
}


def _normalize_code(code: str) -> str:
    """Normalize a stock code to the API format.
    
    - 6-digit codes starting with 6 → Shanghai (sh)
    - Other 6-digit codes → Shenzhen (sz)
    - Already prefixed codes → keep as-is
    """
    code = code.strip().upper()
    if code.startswith(("SH", "SZ")):
        return code.lower()
    if len(code) == 6:
        prefix = "sh" if code.startswith(("5", "6", "9")) else "sz"
        return f"{prefix}{code}"
    return code


def _fetch_quote_raw(codes: list[str]) -> dict[str, list[str]]:
    """Fetch raw quote data from the public API.
    
    Returns a dict mapping code → list of fields.
    """
    normalized = [_normalize_code(c) for c in codes]
    query = ",".join(normalized)
    url = f"{API_BASE}/q={query}"
    
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
    
    # The API returns GBK-encoded text
    text = codecs.decode(raw, "gbk")
    
    result: dict[str, list[str]] = {}
    for line in text.strip().split("\n"):
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        parts = value.split("~")
        result[key] = parts
    
    return result


def _parse_quote(parts: list[str], code: str) -> dict[str, Any]:
    """Parse the raw API response into a structured quote."""
    if len(parts) < 48:
        return {"code": code, "error": "Incomplete data"}
    
    try:
        return {
            "code": code,
            "name": parts[1],
            "price": float(parts[3]),
            "previous_close": float(parts[4]),
            "open": float(parts[5]),
            "volume": int(parts[6]) if parts[6] else 0,
            "high": float(parts[33]),
            "low": float(parts[34]),
            "change": float(parts[31]),
            "change_pct": float(parts[32]),
            "turnover_rate": float(parts[38]) if parts[38] else 0.0,
            "pe_ratio": float(parts[39]) if parts[39] else 0.0,
            "amplitude": float(parts[43]) if parts[43] else 0.0,
            "circulating_market_cap": float(parts[44]) if parts[44] else 0.0,
            "total_market_cap": float(parts[45]) if parts[45] else 0.0,
            "pb_ratio": float(parts[46]) if parts[46] else 0.0,
        }
    except (ValueError, IndexError):
        return {"code": code, "error": "Parse error"}


# ══════════════════════════════════════════════════════════
#  MCP SERVER
# ══════════════════════════════════════════════════════════

server = Server("ticker-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_quote",
            description="Get real-time stock quotes by ticker code(s). "
                        "Supports Chinese A-share stocks (e.g., 600519, 000002). "
                        "No API key required.",
            inputSchema={
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Stock code(s), e.g. ['600519', '000002']",
                    }
                },
                "required": ["codes"],
            },
        ),
        Tool(
            name="get_market_summary",
            description="Get overview of major Chinese market indices "
                        "(Shanghai Composite, Shenzhen Component, CSI 300, etc.)",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_quote":
        codes = arguments.get("codes", [])
        if not codes:
            return [TextContent(type="text", text="Please provide at least one stock code.")]
        
        raw = _fetch_quote_raw(codes)
        results = []
        for code, parts in raw.items():
            results.append(_parse_quote(parts, code))
        
        return [TextContent(
            type="text",
            text=json.dumps(results, ensure_ascii=False, indent=2),
        )]
    
    elif name == "get_market_summary":
        codes = list(INDICES.keys())
        raw = _fetch_quote_raw(codes)
        
        results = []
        for code, parts in raw.items():
            name = INDICES.get(code, code)
            quote = _parse_quote(parts, code)
            quote["index_name"] = name
            results.append(quote)
        
        return [TextContent(
            type="text",
            text=json.dumps(results, ensure_ascii=False, indent=2),
        )]
    
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════

def main():
    import asyncio
    import argparse
    
    parser = argparse.ArgumentParser(description="ticker-mcp: Stock data MCP server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                       help="Transport protocol (default: stdio)")
    parser.add_argument("--port", type=int, default=8080,
                       help="Port for SSE transport (default: 8080)")
    args = parser.parse_args()
    
    if args.transport == "sse":
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn
        
        sse = SseServerTransport("/messages/")
        
        async def handle_sse(request):
            async with sse.connect_sse(
                request.scope, request.receive, request._send,
            ) as streams:
                await server.run(streams[0], streams[1], models.InitializationOptions(
                    server_name="ticker-mcp",
                    server_version="0.1.0",
                ))
        
        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
        ])
        
        print(f"Starting ticker-mcp SSE server on port {args.port}...")
        uvicorn.run(app, host="0.0.0.0", port=args.port)
    else:
        asyncio.run(_run_stdio())


async def _run_stdio():
    async with stdio_server() as streams:
        await server.run(
            streams[0], streams[1],
            models.InitializationOptions(
                server_name="ticker-mcp",
                server_version="0.1.0",
            ),
        )


if __name__ == "__main__":
    main()
