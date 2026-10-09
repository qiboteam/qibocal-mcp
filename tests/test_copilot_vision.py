"""Opt-in subscription vision probe; never connects to calibration hardware."""

import asyncio
import base64
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from fastmcp import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent

from qibocal_mcp import copilot, copilot_server
from qibocal_mcp.calibration import NAME
from qibocal_mcp.deployment import DeploymentSettings, ServerLaunch


def challenge() -> tuple[bytes, list[dict[str, str]]]:
    """Encode random colors/shapes only in pixels, not PNG metadata or text."""
    palette = {"red": (220, 30, 30), "green": (20, 160, 50), "blue": (30, 70, 220)}
    cells = [
        {"color": secrets.choice(list(palette)), "shape": secrets.choice(
            ["circle", "square", "triangle"]
        )}
        for _ in range(6)
    ]
    width, height = 480, 320
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            cell = cells[(y // 160) * 3 + x // 160]
            dx, dy = x % 160 - 80, y % 160 - 80
            if cell["shape"] == "circle":
                inside = dx * dx + dy * dy <= 45 * 45
            elif cell["shape"] == "square":
                inside = abs(dx) <= 45 and abs(dy) <= 45
            else:
                inside = -45 <= dy <= 45 and abs(dx) <= (dy + 45) / 2
            pixels.extend(palette[cell["color"]] if inside else (255, 255, 255))

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )
    return png, cells


def serve(directory: Path) -> None:
    """Serve a synthetic upstream; the real Copilot relay stages its PNG."""
    server = FastMCP("Hardware-free vision probe")

    @server.tool
    def get_calibration_context():
        return {
            "synthetic": True,
            "protocols": {
                "vision_probe": {
                    "parameters": {}, "targets": [0], "hardware_access": False,
                },
            },
        }

    @server.tool
    def execute_protocol(protocol: str, parameters: dict, targets: list[int]):
        if protocol != "vision_probe" or parameters != {} or targets != [0]:
            raise ValueError("Only the hardware-free vision_probe is supported")
        answer = directory / "expected.json"
        if answer.exists():
            raise ValueError("The one-protocol probe budget is exhausted")
        png, cells = challenge()
        answer.write_text(json.dumps(cells), encoding="utf-8")
        return ToolResult(
            content=[
                TextContent(type="text", text='{"run_id":"synthetic","synthetic":true}'),
                ImageContent(
                    type="image", data=base64.b64encode(png).decode(), mimeType="image/png",
                ),
            ],
            structured_content={"run_id": "synthetic", "synthetic": True},
        )

    @server.tool
    def update_platform(run_id: str):
        raise ValueError("Updates are forbidden in this hardware-free probe")

    server.run(transport="stdio", show_banner=False)


class ViewerOnlyReports(copilot_server.StageReports):
    """Suppress inline copies after real staging to isolate viewer delivery."""

    async def on_call_tool(self, context, call_next):
        result = await super().on_call_tool(context, call_next)
        return ToolResult(
            content=[item for item in result.content if not isinstance(item, ImageContent)],
            structured_content=result.structured_content,
            meta=result.meta, is_error=result.is_error,
        )


class CopilotVisionTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("QIBOCAL_COPILOT_VISION_MODEL"),
        "Opt in with QIBOCAL_COPILOT_VISION_MODEL; consumes Copilot subscription credits",
    )
    def test_subscription_model_reads_staged_report_pixels(self):
        executable = copilot.copilot_executable()
        model = os.environ["QIBOCAL_COPILOT_VISION_MODEL"]
        with tempfile.TemporaryDirectory(prefix="qibocal-vision-") as temporary:
            directory = Path(temporary).resolve()
            plugin = directory / "plugin"
            files = copilot.plugin_files(DeploymentSettings(platform="unused"))
            agent_path = f"agents/{NAME}.agent.md"
            _, frontmatter, instructions = files[agent_path].split("---\n", 2)
            profile = json.loads(frontmatter)
            server = copilot.server_config(
                ServerLaunch(
                    command=sys.executable,
                    args=[str(Path(__file__).resolve()), "--serve", str(directory)],
                    env={}, cwd=str(directory),
                ),
                timeout=30,
            )
            server["args"] = [str(Path(__file__).resolve()), "--relay", server["args"][-1]]
            profile["mcp-servers"]["qibocal"] = server
            files[agent_path] = "---\n" + json.dumps(profile, indent=2) + "\n---\n" + instructions
            copilot.write_plugin(plugin, files)
            prompt = (
                "This is a hardware-free visual transport probe, not a calibration. "
                "Approved bounds: synthetic target 0 only, exactly one vision_probe "
                "protocol with parameters {}, no hardware and no platform updates. "
                "Discover context, execute that protocol once, and invoke the view "
                "tool on its copilot_report_images path, even if inline image content "
                "is also visible. Invoking view is required for this probe. "
                "The report PNG contains a 2-row, 3-column grid of colored shapes. "
                "Identify each cell left-to-right across the top row, then the bottom "
                "row. Colors are red, green or blue; shapes are circle, square or "
                "triangle. Do not guess from metadata or read any other files. "
                'Finish with one line VISION_RESULT=[{"color":"...","shape":"..."},'
                "...] containing all six cells, with lowercase values. "
                "If pixels are unavailable, say so and stop. Do not ask questions."
            )
            command = copilot.command_for(plugin, executable, options=[
                "--model", model, "--effort", "low", "--max-ai-credits", "30",
                "--no-custom-instructions", "--no-auto-update", "--no-remote-export",
                "--allow-tool", "qibocal", "--allow-tool", "view",
                "--add-dir", str(directory), "--output-format", "json", "--stream", "off",
                "--prompt", prompt,
            ])
            environment = {
                name: value for name, value in os.environ.items()
                if not name.startswith("COPILOT_PROVIDER_") and name != "COPILOT_OFFLINE"
            }
            environment["XDG_CACHE_HOME"] = str(directory / "cache")
            result = subprocess.run(
                command, cwd=directory, env=environment, capture_output=True,
                text=True, timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout[-2000:])
            events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            diagnostic = "\n".join(
                json.dumps({
                    "type": event["type"],
                    "content": event["data"].get("content"),
                    "tool": event["data"].get("toolName"),
                    "arguments": event["data"].get("arguments"),
                    "message": event["data"].get("message"),
                }) for event in events
                if event["type"] in {"assistant.message", "tool.execution_start", "session.error"}
            )
            response = "\n".join(
                event["data"]["content"] for event in events
                if event["type"] == "assistant.message"
            )
            answer = re.search(r"^VISION_RESULT=(.*)", response, re.MULTILINE | re.DOTALL)
            self.assertIsNotNone(answer, diagnostic + result.stderr)
            calls = [
                event["data"] for event in events if event["type"] == "tool.execution_start"
            ]
            names = [call["toolName"] for call in calls]
            self.assertEqual(names.count("qibocal-get_calibration_context"), 1, diagnostic)
            self.assertEqual(names.count("qibocal-execute_protocol"), 1, diagnostic)
            self.assertNotIn("qibocal-update_platform", names, diagnostic)
            expected = directory / "expected.json"
            self.assertTrue(expected.is_file(), "The synthetic MCP tool was not called")
            images = list(
                (directory / "cache").glob("qibocal-mcp/copilot-reports/*/*/report-0.png")
            )
            self.assertEqual(len(images), 1, "Expected one report staged by the native relay")
            viewers = [call for call in calls if call["toolName"] == "view"]
            self.assertTrue(viewers, "Copilot did not use the native viewer:\n" + diagnostic)
            self.assertTrue(any(
                call["arguments"].get("path") == str(images[0]) for call in viewers
            ), "Copilot did not view the staged report:\n" + diagnostic)
            self.assertTrue(any(
                event["type"] == "tool.execution_complete"
                and event["data"]["toolCallId"] in {call["toolCallId"] for call in viewers}
                and event["data"]["success"]
                for event in events
            ), "The native viewer did not succeed:\n" + diagnostic)
            actual, _ = json.JSONDecoder().raw_decode(answer.group(1).strip())
            self.assertEqual(actual, json.loads(expected.read_text()))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "--relay":
        with patch.object(copilot_server, "StageReports", ViewerOnlyReports):
            asyncio.run(copilot_server.run(sys.argv[2]))
    else:
        unittest.main()
