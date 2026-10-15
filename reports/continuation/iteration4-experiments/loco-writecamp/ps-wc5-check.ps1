
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*wc5-0e5e2d1a-b*' -or $_.CommandLine -like '*run_lmes_wc*' } | ForEach-Object {
  Write-Output ("PID=" + $_.ProcessId + " NAME=" + $_.Name)
  Write-Output ("  CMD=" + $_.CommandLine.Substring(0, [Math]::Min(180, $_.CommandLine.Length)))
}
