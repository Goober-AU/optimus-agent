
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'cmd.exe /c ""C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-writecamp/reports/continuation/iteration4-experiments/loco-writecamp/run-gate-q10.cmd""' }
Write-Output ('gateq10d: rc=' + $result.ReturnValue + ' pid=' + $result.ProcessId)
