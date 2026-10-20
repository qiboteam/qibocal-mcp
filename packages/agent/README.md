# qibocal-agent

Orchestration for the [`qibocal-mcp`](../mcp) calibration servers, installable
on any machine (the base package only needs FastMCP, not the Qibocal stack):

- `qibocal-mcp-remote` — start the calibration MCP from your laptop: it
  launches the server over SSH on the access node (provisioning a managed
  environment there if needed) and serves it over stdio for any MCP client,
  with stored configurations switchable from within the conversation;
- `qibocal-agent` — autonomous Pydantic AI calibration agent (install the
  `agent` extra);
- `qibocal-report-server` — browse the generated calibration reports, locally
  or through an SSH tunnel;
- `qibocal-mcp-config` — persistent defaults, so launches reduce to a name.

Full documentation lives in the repository `docs/`.
