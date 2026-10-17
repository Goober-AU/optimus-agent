@echo off
powershell -NoProfile -ExecutionPolicy Bypass -Command "& 'C:/Users/openclawuser/.net-ipc/netbuild.ps1' -AgentName ipc-bench -CargoArgs @('check','-p','pi-coding-agent','--lib','--tests','--offline','--locked')" > C:/Users/openclawuser/optimus-net-20261017/research/network_latency/ipc/results/check-run.log 2>&1
