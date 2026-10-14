Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*sleeper*' } | ForEach-Object { Write-Output ('PID=' + $_.ProcessId + ' ' + $_.Name) }
