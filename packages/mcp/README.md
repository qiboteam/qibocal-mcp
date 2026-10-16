# qibocal-mcp

MCP servers exposing [Qibocal](https://github.com/qiboteam/qibocal) calibration
to any MCP-capable interface. One package, two servers, one command:

- `qibocal-mcp --platform NAME` — direct hardware server, run it where the
  instruments are reachable (a lab workstation or a SLURM compute node);
- `qibocal-mcp --platform NAME --partition QUEUE` — SLURM gateway, run it on
  the access node: every operation is dispatched through `srun`.

Both serve the same tools over stdio, plus the reusable `calibrate` prompt
(also exposed as a tool for interfaces that ignore MCP prompts).

To reach these servers from your own machine over SSH, and for managed
environments, persistent configuration and the autonomous agent, see the
companion [`qibocal-agent`](../agent) package. Full documentation lives in the
repository `docs/`.
