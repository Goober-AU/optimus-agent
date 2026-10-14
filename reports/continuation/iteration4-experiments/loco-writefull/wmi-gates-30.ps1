$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'cmd.exe /c ""C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-writefull\reports/continuation/iteration4-experiments/loco-writefull\run-gates-30.cmd""' }
Write-Output ('wmi-gates-30: rc=' + $result.ReturnValue + ' pid=' + $result.ProcessId)
