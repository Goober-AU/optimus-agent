
$cmdline = 'cmd /c C:\Users\openclawuser\optimus-memory-bench-20261007-040424\imp4b-lpcamp\reports\continuation\iteration4\lpcamp\run-lpcamp-pipeline.cmd'
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmdline }
"WMI Create returned: pid=" + $r.ReturnValue + " processid=" + $r.ProcessId
