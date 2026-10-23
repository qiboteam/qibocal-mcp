"""Report plots, for the model, for the user, and for MCP Apps interfaces.

execute_protocol returns its report images as PNG content, which the model
inspects. The user may not see tool results, so every result with plots opens
with a reminder to show them. Its wording follows the connected client: one
supporting MCP Apps (SEP-1865) renders the view below next to the tool call,
and is told not to repeat the plots; any other is asked to embed them. Each
server in a chain rewrites the reminder for its own client, so the one facing
the user's interface decides.

The view draws each plot interactively when its Plotly JSON is light enough,
fetched on demand through an app-only tool, and shows the PNG otherwise. It is
plain HTML, kept in this module since deployment only ships Python files, and
loads plotly.js from its CDN at the version that produced the figures.
"""

import json
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.apps import UI_EXTENSION_ID, AppConfig, ResourceCSP
from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from mcp.types import TextContent

VIEW = "ui://qibocal/plots.html"
FIGURE_TOOL = "get_run_figures"
CDN = "https://cdn.plot.ly"
APP = AppConfig(resourceUri=VIEW)
# Hidden from the model by interfaces supporting MCP Apps.
APP_ONLY = AppConfig(visibility=["app"])
# Heavier figures (e.g. raw single shots) are only shown as images.
FIGURE_LIMIT = 200_000
# Marks the reminder block, so that outer servers can replace it.
REMINDER = "qibocal/reminder"
PLOTTING_TOOLS = {"execute_protocol", "get_run"}

HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Qibocal plots</title>
<style>
  body { margin: 0; font-family: system-ui, sans-serif; font-size: 13px; }
  .target { margin: 8px 4px 0; font-weight: 600; }
  .figure { width: 100%; height: 450px; }
  img.figure { height: auto; }
  #status { margin: 8px 4px; opacity: 0.7; }
</style>
</head>
<body>
<div id="status">Waiting for the protocol results...</div>
<div id="plots"></div>
<script>
"use strict";
const CDN = __CDN__;
const FIGURE_TOOL = __FIGURE_TOOL__;
const pending = new Map();
let next = 1;

function send(message) {
  window.parent.postMessage({ jsonrpc: "2.0", ...message }, "*");
}

function request(method, params) {
  const id = next++;
  send({ id, method, params });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}

function resize() {
  send({
    method: "ui/notifications/size-changed",
    params: {
      width: document.documentElement.scrollWidth,
      height: document.documentElement.scrollHeight,
    },
  });
}

function status(text) {
  const element = document.getElementById("status");
  element.textContent = text;
  element.hidden = !text;
  resize();
}

function plotly(version) {
  if (window.Plotly) return Promise.resolve(window.Plotly);
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `${CDN}/plotly-${version}.min.js`;
    script.onload = () => resolve(window.Plotly);
    script.onerror = () => reject(new Error(`Could not load ${script.src}`));
    document.head.appendChild(script);
  });
}

async function figures(runId) {
  // Fetched on demand, so that results stay light for every client.
  const result = await request("tools/call", { name: FIGURE_TOOL, arguments: { run_id: runId } });
  return result.structuredContent;
}

async function render(result) {
  const run = result.structuredContent || {};
  const plots = run.plots || [];
  if (!plots.length) {
    status(result.isError ? "The protocol failed."
      : run.status ? `Run ${run.status}: its plots appear with get_run's result.`
      : "This run has no plots.");
    return;
  }
  status("Loading plots...");
  const images = (result.content || []).filter((block) => block.type === "image");
  let interactive = { figures: {} };
  try {
    if (plots.some((plot) => plot.interactive)) interactive = await figures(run.run_id);
  } catch (error) {
    // Images remain available.
  }
  try {
    const container = document.getElementById("plots");
    container.replaceChildren();
    let target = null;
    for (const [index, plot] of plots.entries()) {
      if (plot.target !== target) {
        target = plot.target;
        const title = document.createElement("div");
        title.className = "target";
        title.textContent = `Target ${target}`;
        container.appendChild(title);
      }
      const figure = interactive.figures[index];
      if (figure) {
        const Plotly = await plotly(interactive.plotly);
        const element = document.createElement("div");
        element.className = "figure";
        container.appendChild(element);
        await Plotly.newPlot(element, figure.data, figure.layout, { responsive: true });
      } else if (images[index]) {
        const element = document.createElement("img");
        element.className = "figure";
        element.src = `data:${images[index].mimeType};base64,${images[index].data}`;
        element.onload = resize;
        container.appendChild(element);
      }
    }
    status("");
  } catch (error) {
    status(String(error));
  }
}

window.addEventListener("message", (event) => {
  const message = event.data;
  if (!message || message.jsonrpc !== "2.0") return;
  if (message.method === undefined && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  } else if (message.method === "ui/notifications/tool-result") {
    render(message.params);
  } else if (message.id !== undefined && message.method !== undefined) {
    // Host requests (ping, teardown) need no work here.
    send({ id: message.id, result: {} });
  }
});

window.addEventListener("resize", resize);

request("ui/initialize", {
  appInfo: { name: "qibocal-plots", version: "1.0.0" },
  appCapabilities: {},
  protocolVersion: "2026-01-26",
}).then(() => {
  send({ method: "ui/notifications/initialized", params: {} });
  resize();
}).catch((error) => status(`Could not connect to the host: ${error.message}`));
</script>
</body>
</html>
"""


def view() -> str:
    """Interactive Plotly figures of the protocol reports, or their images."""
    return HTML.replace("__CDN__", json.dumps(CDN)).replace(
        "__FIGURE_TOOL__", json.dumps(FIGURE_TOOL)
    )


def register_plot_view(mcp: FastMCP) -> None:
    """Serve the view, and rewrite the plot reminder for this server's client."""
    mcp.resource(
        VIEW, name="qibocal-plots",
        app=AppConfig(csp=ResourceCSP(resourceDomains=[CDN])),
    )(view)
    mcp.add_middleware(ShowPlots())


def save_plots(folder: Path, entries: list[tuple[object, list]]) -> list[dict]:
    """Write each target's figures as HTML, PNG and, when light, Plotly JSON."""
    plots = []
    for index, (target, figures) in enumerate(entries):
        for number, figure in enumerate(figures):
            stem = folder / f"report-{index}-{number}"
            figure.write_html(stem.with_suffix(".html"))
            figure.write_image(stem.with_suffix(".png"), width=1200, height=700)
            data = figure.to_json()
            interactive = len(data) <= FIGURE_LIMIT
            if interactive:
                stem.with_suffix(".json").write_text(data, encoding="utf-8")
            plots.append({
                "target": str(target), "image": str(stem.with_suffix(".png")),
                "interactive": interactive,
            })
    return plots


def load_figures(plots: list[dict]) -> dict:
    """The interactive figures of a run, by plot index, for the view."""
    # Only the hardware process has Plotly.
    import plotly

    return {
        "plotly": plotly.offline.get_plotlyjs_version(),
        "figures": {
            str(index): json.loads(
                Path(plot["image"]).with_suffix(".json").read_text(encoding="utf-8")
            )
            for index, plot in enumerate(plots)
            if plot["interactive"]
        },
    }


def reminder(plots: list[dict], displayed: bool) -> str:
    """Ask to show the plots, or not to repeat those the interface displays."""
    if displayed:
        return (
            f"The interface shows this run's {len(plots)} plots to the user next to "
            "this tool call. Before calling any other tool, send the user your "
            "reading of each fit and your update decision, referring to the plots; "
            "do not embed them again."
        )
    listing = "\n".join(
        f"{index}. target {plot['target']}: {plot['image']}"
        for index, plot in enumerate(plots, start=1)
    )
    return (
        "The user cannot see tool results. Before calling any other tool, send the "
        f"user a message embedding these {len(plots)} report images, in this order, "
        "with your reading of each fit and your update decision. If you cannot "
        f"embed images, give their paths:\n{listing}"
    )


class ShowPlots(Middleware):
    """Open results with plots by the reminder suited to this server's client."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)
        plots = (result.structured_content or {}).get("plots")
        if context.message.name not in PLOTTING_TOOLS or not plots:
            return result
        displayed = (
            context.fastmcp_context is not None
            and context.fastmcp_context.client_supports_extension(UI_EXTENSION_ID)
        )
        content = [
            block for block in result.content
            if not (block.meta or {}).get(REMINDER)
        ]
        note = TextContent(
            type="text", text=reminder(plots, displayed), _meta={REMINDER: True},
        )
        return ToolResult(
            content=[note, *content], structured_content=result.structured_content,
            meta=result.meta,
        )
