"""Internal stdio relay for a Copilot-generated, resolved server launch."""

import base64
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
from mcp.types import CallToolRequestParams, ImageContent, TextContent

from .deployment import ServerLaunch


def decode_launch(encoded: str) -> tuple[ServerLaunch, float]:
    """Restore literal arguments/directory and runtime-only environment."""
    launch = json.loads(base64.b64decode(encoded, validate=True))
    if (
        not isinstance(launch, dict)
        or not isinstance(launch.get("command"), str)
        or not launch["command"]
        or not isinstance(launch.get("args"), list)
        or any(not isinstance(arg, str) for arg in launch["args"])
        or not isinstance(launch.get("env"), list)
        or any(not isinstance(name, str) for name in launch["env"])
        or (launch.get("cwd") is not None and not isinstance(launch["cwd"], str))
        or isinstance(launch.get("timeout"), bool)
        or not isinstance(launch.get("timeout"), (int, float))
        or not 0.001 <= launch["timeout"] <= 2147483.647
    ):
        raise ValueError("Invalid Copilot server launch description")
    environment = {}
    for name in launch["env"]:
        if name not in os.environ or os.environ[name] == f"${{{name}}}":
            raise ValueError(f"Copilot did not forward environment variable {name}")
        environment[name] = os.environ[name]
    return ServerLaunch(
        command=launch["command"], args=launch["args"],
        env=environment, cwd=launch.get("cwd"),
    ), launch["timeout"]


class StageReports(Middleware):
    """Expose client-local PNG paths for Copilot's native image reader."""

    def __init__(self, directory: Path):
        self.directory = directory

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        images = [item for item in result.content if isinstance(item, ImageContent)]
        if not images or result.is_error:
            return result
        folder = self.directory / uuid4().hex
        folder.mkdir(parents=True, mode=0o700)
        paths = []
        for index, image in enumerate(images):
            if image.mimeType != "image/png":
                raise ValueError(f"Unsupported report image type: {image.mimeType}")
            data = base64.b64decode(image.data, validate=True)
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Report image is not a PNG")
            path = folder / f"report-{index}.png"
            with path.open("xb") as stream:
                stream.write(data)
            paths.append(str(path))
        staged = {
            "copilot_report_images": paths,
            "inspection_required": True,
            "instruction": "Use view on every client-local PNG before considering an update.",
        }
        return ToolResult(
            content=[*result.content, TextContent(type="text", text=json.dumps(staged))],
            structured_content=(
                {**result.structured_content, **staged}
                if result.structured_content is not None else None
            ),
            meta=result.meta, is_error=result.is_error,
        )


def report_proxy(client: Client, directory: Path) -> FastMCP:
    """Reuse upstream schemas and session; adapt only report presentation."""
    if not client.is_connected():
        raise ValueError("Report proxy requires a connected upstream session")
    proxy = create_proxy(client, name="Qibocal Copilot")
    proxy.add_middleware(StageReports(directory))
    return proxy


async def run(encoded: str) -> None:
    """Keep one upstream session alive while serving the native image adapter."""
    launch, timeout = decode_launch(encoded)
    transport = StdioTransport(
        command=launch.command, args=launch.args, cwd=launch.cwd,
        env=launch.env, keep_alive=False,
    )
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    directory = (root / "qibocal-mcp/copilot-reports" / uuid4().hex).resolve()
    async with Client(transport, timeout=timeout, init_timeout=timeout) as client:
        proxy = report_proxy(client, directory)
        await proxy.run_async(transport="stdio", show_banner=False)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("Expected one encoded server launch description")
    try:
        asyncio.run(run(sys.argv[1]))
    except (OSError, ValueError) as error:
        print(f"Could not launch Qibocal server: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
