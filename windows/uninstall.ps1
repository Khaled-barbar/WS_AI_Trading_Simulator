# Removes the scheduled tasks and the desktop shortcut (keeps your data)
Unregister-ScheduledTask -TaskName "AgentsLab Daily" -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath("Startup")) "Agents Lab Dashboard.lnk") -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like "*lab dashboard*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item (Join-Path ([Environment]::GetFolderPath("Desktop")) "Agents Lab.lnk") -ErrorAction SilentlyContinue
Write-Host "Removed."; Read-Host "Press Enter to close"
