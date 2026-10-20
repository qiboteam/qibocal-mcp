"""One SLURM job: use the same MCP implementation, then disconnect and exit."""

import argparse
import asyncio
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from fastmcp import Client
from mcp.types import CallToolResult, TextContent, TextResourceContents

from .runtime import create_server


async def run(
    platform: str,
    *,
    output: Path,
    snapshot: Path | None,
    request: dict,
    session_id: str | None = None,
) -> CallToolResult:
    """Restore accepted state and execute exactly one requested operation."""
    with redirect_stdout(sys.stderr):
        server = create_server(
            platform, output=output, snapshot=snapshot, restore_runs=True,
            session_id=session_id,
        )
        async with Client(server) as client:
            if request["operation"] == "context":
                content = await client.read_resource("qibocal://context")
                if len(content) != 1 or not isinstance(content[0], TextResourceContents):
                    raise ValueError("Server returned an invalid context resource")
                return CallToolResult(
                    content=[TextContent(type="text", text=content[0].text)]
                )
            if request["operation"] not in {
                "execute_protocol", "update_platform", "start_session",
                "get_platform_state", "patch_platform",
                "append_note", "get_notes",
            }:
                raise ValueError(f"Unknown worker operation: {request['operation']}")
            result = await client.call_tool(
                request["operation"], request["arguments"]
            )
            return CallToolResult(
                content=result.content, structuredContent=result.structured_content
            )


def main():
    """Read a JSON request from stdin; emit only the JSON response on stdout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    result = asyncio.run(
        run(
            args.platform,
            output=args.output,
            snapshot=args.snapshot,
            request=json.load(sys.stdin),
            session_id=args.session_id,
        )
    )
    print(result.model_dump_json(by_alias=True))


if __name__ == "__main__":
    main()
