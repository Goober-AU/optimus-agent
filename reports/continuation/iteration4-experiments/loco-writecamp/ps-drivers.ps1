
Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'memory_bench.exe' } | ForEach-Object {
  Write-Output ("PID=" + $_.ProcessId + " START=" + $_.CreationDate)
  Write-Output ("  CMD=" + $_.CommandLine)
}
