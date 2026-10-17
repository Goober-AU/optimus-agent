# Session average output speed

The terminal agent bar shows a red `AVG TPS` immediately after `OCC2`.
It includes the current chat and its observed subagent descendants, excluding
unrelated chats. The value updates during streaming (at most four roster broadcasts per second)
and remains visible while idle. Before output arrives it shows `AVG TPS —`.
While a response is in flight, `~` marks an estimate: streamed text, reasoning,
and tool-argument Unicode characters divided by four. This is a heuristic, not
a model tokenizer. Final provider usage replaces that response's estimate
without double counting; a terminal response without valid usage drops its
provisional contribution. Hidden reasoning cannot be counted before provider usage arrives.

The calculation is `sum(output tokens) / sum(provider request seconds)`, including
the current in-flight estimate alongside completed provider totals.
This is a duration-weighted average, not the sum of parallel agents' rates.
Request time runs from dispatch to the network terminal event, falling back to
measured request completion when the provider has no network-terminal timing.
It includes response latency; it excludes queue/pre-dispatch waiting, tool
execution and idle time. Final provider output usage is authoritative; input/cache
counts and logical-request totals are not added. Failed/cancelled attempts count
only when they report valid output usage and duration.

Counters are in memory for each worker session incarnation. They start after
that worker is opened and do not reconstruct old transcript history. Worker
restarts reset that worker's counters. The UI retains the last measured totals
for observed children that leave the roster while this chat remains open.
Switching chats or losing support clears the display. `/monitor` controls disk
logging independently; this indicator does not enable logging or provider calls.

Daemon protocol 7, schema 43 extends optional `generationThroughput` roster metadata
behind the `generation_throughput` capability with optional `active`, `estimated`,
and monotonic `sequence` fields. Schema-42 peers still support completed totals.
Existing commands and event
compatibility floors remain unchanged. Old daemons display no measurement;
old clients can ignore the new metadata. This is a terminal presentation feature
in Optimus; no Telemus 3D or Web source is changed.

# Targeted shutdown

`optimus-agent shutdown --socket <path>` stops only the selected daemon.
`--daemon-socket` is an alias, and either socket option may precede `shutdown`.
An interactive confirmation is required unless `--force` is supplied. `--json`
requires `--force` because it cannot prompt. Invalid or repeated socket options
fail instead of falling back to shutting down all services.
Without a socket option, the existing all-services shutdown behavior is retained.
