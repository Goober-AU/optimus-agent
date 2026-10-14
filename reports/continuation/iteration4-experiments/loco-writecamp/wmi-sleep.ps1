
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'cmd.exe /c ""C:/Users/openclawuser/optimus-memory-bench-20261007-040424/repo-continuation/bench/.venv/Scripts/python.exe" -c "import time; time.sleep(60)" > "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-writecamp/reports/continuation/iteration4-experiments/loco-writecamp/wmi-sleep.log" 2>&1"' }
Write-Output ('sleep: rc=' + $result.ReturnValue + ' pid=' + $result.ProcessId)
