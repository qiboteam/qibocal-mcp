"""Interactive report plots, for chat interfaces that render MCP Apps.

execute_protocol returns its report images as PNG content, which the model
inspects. The same figures travel as Plotly JSON in the result metadata, which
the model does not see: interfaces supporting MCP Apps (SEP-1865) render them
with the view below, next to the tool call. Others ignore both.

The view is plain HTML, kept in this module since deployment only ships the
server's Python files. It loads plotly.js from its CDN, at the version of the
Plotly that produced the figures.
"""

import json

from fastmcp import FastMCP
from fastmcp.apps import AppConfig, ResourceCSP

VIEW = "ui://qibocal/plots.html"
# Result metadata key holding {"plotly": version, "figures": [...]}.
FIGURES = "qibocal/figures"
CDN = "https://cdn.plot.ly"
APP = AppConfig(resourceUri=VIEW)

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
  #status { margin: 8px 4px; opacity: 0.7; }
</style>
</head>
<body>
<div id="status">Waiting for the protocol results...</div>
<div id="plots"></div>
<script>
"use strict";
const CDN = __CDN__;
const FIGURES = __FIGURES__;
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

async function render(result) {
  const report = (result._meta || {})[FIGURES];
  if (!report || !report.figures.length) {
    status(result.isError ? "The protocol failed." : "This run has no plots.");
    return;
  }
  status("Loading plots...");
  try {
    const Plotly = await plotly(report.plotly);
    const container = document.getElementById("plots");
    container.replaceChildren();
    let target = null;
    for (const figure of report.figures) {
      if (figure.target !== target) {
        target = figure.target;
        const title = document.createElement("div");
        title.className = "target";
        title.textContent = `Target ${target}`;
        container.appendChild(title);
      }
      const element = document.createElement("div");
      element.className = "figure";
      container.appendChild(element);
      await Plotly.newPlot(element, figure.data, figure.layout, { responsive: true });
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
    """Interactive Plotly figures of the protocol reports."""
    return HTML.replace("__CDN__", json.dumps(CDN)).replace(
        "__FIGURES__", json.dumps(FIGURES)
    )


def register_plot_view(mcp: FastMCP) -> None:
    """Serve the view; tools opt in with app=APP."""
    mcp.resource(
        VIEW, name="qibocal-plots",
        app=AppConfig(csp=ResourceCSP(resourceDomains=[CDN])),
    )(view)


def figures_meta(entries: list[tuple[object, list]]) -> dict:
    """Result metadata with the figures of each target, as Plotly JSON."""
    # Only the hardware process has Plotly.
    import plotly

    return {FIGURES: {
        "plotly": plotly.offline.get_plotlyjs_version(),
        "figures": [
            {"target": str(target), **json.loads(figure.to_json())}
            for target, figures in entries
            for figure in figures
        ],
    }}
