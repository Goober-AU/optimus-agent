# Agent resource status

The fullscreen header shows a neon-blue strip above the Optimus branding:

```text
MAIN CPU 12% RAM 512MiB TPS 50.0 │ tests CPU 3% RAM 128MiB TPS ~75.0
```

- CPU is local worker process CPU usage, sampled once per second. One fully
  occupied logical CPU is 100%; a multithreaded worker can exceed 100%.
- RAM is that process's resident memory, in MiB or GiB. It excludes separate tool
  subprocesses, the terminal, the daemon supervisor and remote model servers.
- TPS is each agent's duration-weighted streaming output rate from the existing
  roster telemetry. It excludes time waiting for the first token and tool time.
  `~` marks a live estimate. Finalized measurements remain visible while idle;
  these are streaming averages, not wall-clock tokens per second.
- `[shared]` means multiple sessions run in the same worker process. CPU/RAM
  describe that shared process and must not be added together as separate costs.
  Each session's TPS remains independent.
- A dash means unavailable or still warming up. CPU needs two samples. Readings
  expire after three seconds and reset on session or worker changes. CPU/RAM are
  shown only when a local worker descriptor and process identity can be verified.

The selected main agent and its descendants use the same family relationships as
the agents list. Long groups wrap within the header's height budget; overflowing
pages rotate every four seconds and show a page counter. In a one-row strip,
MAIN and workers rotate together. Up to 256 agents are sampled; `+N` indicates
additional agents beyond that limit. Very small terminal heights prioritise the
editor and model metadata.

Sampling runs on a background thread and stops when the chat's status bar closes.
Painting never reads processes or calls the daemon. Existing roster subscriptions
supply identities and throughput; no new daemon commands or wire fields are used.

This is a terminal presentation feature. It applies to the fullscreen header in
all terminal themes. Inline/print/RPC output has no persistent header. Optimus has
no separate 3D or Web dashboard implementation in this repository.
