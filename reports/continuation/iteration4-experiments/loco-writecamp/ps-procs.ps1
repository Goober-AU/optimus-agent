
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run-jobs.py*' -or $_.CommandLine -like '*memory_question_loop*' } | ForEach-Object {
  Write-Output ("PID=" + $_.ProcessId + " NAME=" + $_.Name + " START=" + $_.CreationDate)
  Write-Output ("  CMD=" + $_.CommandLine.Substring(0, [Math]::Min(260, $_.CommandLine.Length)))
}
