# qibocal-agent

Orchestration for the [`qibocal-mcp`](../mcp) calibration servers, installable
on any machine (the base package only needs click and FastMCP, not the Qibocal
stack). It installs one command, `qibocal-agent`, with a subcommand per task:

- `qibocal-agent mcp` — start the calibration MCP from your laptop: it
  launches the server over SSH on the access node (provisioning a managed
  environment there if needed) and serves it over stdio for any MCP client,
  with stored configurations switchable from within the conversation;
- `qibocal-agent run` — autonomous Pydantic AI calibration agent (install the
  `agent` extra);
- `qibocal-agent report` — browse the generated calibration reports, locally
  or through an SSH tunnel;
- `qibocal-agent config` — manage the stored configurations, in
  `~/.config/qibocal/agents.json`, so launches reduce to a name.

`qibocal-agent --help`, and `--help` on each subcommand, describe the options.
Full documentation lives in the repository `docs/`.
