"""
toolkit-mcp — MCP server for developer utilities.

A well-architected MCP server demonstrating clean patterns for:
- Tool definition with typed schemas
- Input validation and error handling
- Extensible handler registration
- Multiple transport modes (stdio / SSE)

Tools:
    validate_json    — Validate and format JSON strings
    json_to_schema   — Infer JSON Schema from example data
    base64           — Encode or decode base64
    timestamp        — Convert unix timestamp to readable date
    hash             — Hash a string (md5, sha256, sha512)
"""

from __future__ import annotations

import base64 as _base64
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Union

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
#  FRAMEWORK — Reusable MCP server architecture
# ══════════════════════════════════════════════════════════

class ToolRegistry:
    """A registry of MCP tools with typed schemas and handlers.
    
    This is the core architectural pattern — separate tool definition
    from implementation, with automatic schema generation.
    """
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._handlers: dict[str, Callable] = {}
    
    def tool(self, name: str, description: str = "") -> Callable:
        """Decorator: register a function as an MCP tool.
        
        The function's type hints are used to generate the JSON Schema.
        Supports: str, int, float, bool, list[str], dict, Optional types.
        """
        def decorator(func: Callable) -> Callable:
            schema = self._infer_schema(func)
            self._tools[name] = Tool(
                name=name,
                description=description or func.__doc__ or "",
                inputSchema=schema,
            )
            self._handlers[name] = func
            return func
        return decorator
    
    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())
    
    async def call_tool(self, name: str, arguments: dict) -> list[TextContent]:
        if name not in self._handlers:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        
        try:
            result = self._handlers[name](**arguments)
            if isinstance(result, (dict, list)):
                text = json.dumps(result, ensure_ascii=False, indent=2)
            else:
                text = str(result)
            return [TextContent(type="text", text=text)]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]
    
    @staticmethod
    def _infer_schema(func: Callable) -> dict:
        """Infer JSON Schema from a function's type annotations."""
        import inspect
        sig = inspect.signature(func)
        props = {}
        required = []
        
        type_map = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            list: {"type": "array", "items": {}},
            dict: {"type": "object"},
        }
        
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            hint = param.annotation if param.annotation != inspect.Parameter.empty else str
            # Handle Optional / Union types
            origin = getattr(hint, "__origin__", None)
            if origin is type(Union) and type(None) in hint.__args__:
                # Optional type
                actual_type = [t for t in hint.__args__ if t is not type(None)][0]
                schema_entry = type_map.get(actual_type, {"type": "string"}).copy()
                schema_entry["type"] = [schema_entry["type"], "null"]
            else:
                schema_entry = type_map.get(hint, {"type": "string"}).copy()
            
            schema_entry["description"] = f"Parameter {name}"
            
            if param.default == inspect.Parameter.empty:
                required.append(name)
            else:
                schema_entry["default"] = param.default
            
            props[name] = schema_entry
        
        return {
            "type": "object",
            "properties": props,
            "required": required,
        }


# ══════════════════════════════════════════════════════════
#  TOOLS — Developer utilities
# ══════════════════════════════════════════════════════════

registry = ToolRegistry()


@registry.tool("validate_json", "Validate and pretty-print a JSON string")
def validate_json(json_string: str, indent: int = 2) -> dict:
    """Parse and format a JSON string. Returns the parsed data or an error."""
    try:
        data = json.loads(json_string)
        return {
            "valid": True,
            "data": data,
            "formatted": json.dumps(data, ensure_ascii=False, indent=indent),
        }
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "error": str(e),
            "position": e.pos,
            "line": e.lineno,
            "column": e.colno,
        }


@registry.tool("json_to_schema", "Infer a JSON Schema from example JSON data")
def json_to_schema(json_string: str) -> dict:
    """Analyze example JSON and generate a compatible JSON Schema draft-07."""
    try:
        data = json.loads(json_string)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON: {e}"}
    
    def infer_type(value: Any) -> dict:
        if isinstance(value, bool):
            return {"type": "boolean"}
        elif isinstance(value, int):
            return {"type": "integer"}
        elif isinstance(value, float):
            return {"type": "number"}
        elif isinstance(value, str):
            return {"type": "string"}
        elif isinstance(value, list):
            if value:
                items = infer_type(value[0])
                return {"type": "array", "items": items}
            return {"type": "array"}
        elif isinstance(value, dict):
            props = {}
            for k, v in value.items():
                props[k] = infer_type(v)
            return {"type": "object", "properties": props}
        elif value is None:
            return {"type": "null"}
        return {"type": "string"}
    
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        **infer_type(data),
    }


@registry.tool("base64", "Encode or decode base64 strings")
def base64_util(text: str, action: str = "encode") -> str:
    """Encode text to base64 or decode base64 to text.
    
    Args:
        text: The text to encode or decode
        action: 'encode' or 'decode'
    """
    if action == "encode":
        encoded = _base64.b64encode(text.encode()).decode()
        return encoded
    elif action == "decode":
        try:
            decoded = _base64.b64decode(text.encode()).decode()
            return decoded
        except Exception as e:
            return f"Decode failed: {e}"
    else:
        return f"Invalid action: {action}. Use 'encode' or 'decode'."


@registry.tool("timestamp", "Convert a unix timestamp (seconds since epoch) to a human-readable date")
def timestamp_util(seconds: float, utc: bool = False) -> dict:
    """Convert seconds since epoch to a formatted datetime.
    
    Args:
        seconds: Unix timestamp in seconds
        utc: If True, output UTC time. Otherwise local time.
    """
    tz = timezone.utc if utc else None
    dt = datetime.fromtimestamp(seconds, tz=tz)
    return {
        "unix": seconds,
        "iso": dt.isoformat(),
        "formatted": dt.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "UTC" if utc else "local",
    }


@registry.tool("hash", "Hash a string using common algorithms")
def hash_util(text: str, algorithm: str = "sha256") -> str:
    """Compute the hash of a string.
    
    Args:
        text: The string to hash
        algorithm: One of md5, sha1, sha256, sha512
    """
    algo_map = {
        "md5": hashlib.md5,
        "sha1": hashlib.sha1,
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
    }
    
    if algorithm not in algo_map:
        available = ", ".join(algo_map.keys())
        return f"Unsupported algorithm: {algorithm}. Available: {available}"
    
    return algo_map[algorithm](text.encode()).hexdigest()


# ══════════════════════════════════════════════════════════
#  SERVER — MCP protocol server
# ══════════════════════════════════════════════════════════

server = Server("toolkit-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return registry.list_tools()


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return await registry.call_tool(name, arguments)


# ══════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════

def main():
    import asyncio
    import argparse
    
    parser = argparse.ArgumentParser(
        description="toolkit-mcp: Developer utilities MCP server"
    )
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port for SSE transport (default: 8080)",
    )
    args = parser.parse_args()
    
    print(f"Starting toolkit-mcp ({args.transport})...")
    print(f"Tools loaded: {len(registry.list_tools())}")
    for tool in registry.list_tools():
        print(f"  - {tool.name}: {tool.description}")
    
    if args.transport == "sse":
        _run_sse(args.port)
    else:
        asyncio.run(_run_stdio())


def _run_sse(port: int):
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    import uvicorn
    
    sse = SseServerTransport("/messages/")
    
    async def handle_sse(request):
        async with sse.connect_sse(
            request.scope, request.receive, request._send,
        ) as streams:
            await server.run(
                streams[0], streams[1],
                models.InitializationOptions(
                    server_name="toolkit-mcp",
                    server_version="0.1.0",
                ),
            )
    
    app = Starlette(routes=[
        Route("/sse", endpoint=handle_sse),
        Route("/messages/", endpoint=sse.handle_post_message, methods=["POST"]),
    ])
    
    uvicorn.run(app, host="0.0.0.0", port=port)


async def _run_stdio():
    async with stdio_server() as streams:
        await server.run(
            streams[0], streams[1],
            models.InitializationOptions(
                server_name="toolkit-mcp",
                server_version="0.1.0",
            ),
        )


if __name__ == "__main__":
    main()
