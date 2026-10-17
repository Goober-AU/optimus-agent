@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'C:/Users/openclawuser/.net-ipc/netbuild.ps1' -AgentName ipc-bench -CargoArgs @('test','-p','pi-coding-agent','--release','--lib','agent_message_bench','--','--ignored','--nocapture','--test-threads=1')" > C:/Users/openclawuser/optimus-net-20261017/research/network_latency/ipc/results/bench-run.log 2>&1
