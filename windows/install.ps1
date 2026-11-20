# Agents Lab - Windows setup. Run once: right-click > "Run with PowerShell"
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Write-Host "Agents Lab folder: $root"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Host "Python launcher 'py' not found. Install Python first:  winget install Python.Python.3.13" -ForegroundColor Red
    Read-Host "Press Enter to exit"; exit 1
}
& py -3 -c "import sys; assert sys.version_info >= (3,11), sys.version" 
if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.11+ required" -ForegroundColor Red; Read-Host; exit 1 }

# 1) Daily task at 14:00. StartWhenAvailable = runs as soon as the PC is on if 14:00 was missed.
$a = New-ScheduledTaskAction -Execute (Join-Path $root "run_daily.bat") -WorkingDirectory $root
$t = New-ScheduledTaskTrigger -Daily -At "14:00"
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "AgentsLab Daily" -Action $a -Trigger $t -Settings $s -Force | Out-Null
Write-Host "Task 'AgentsLab Daily' created (every day 14:00)." -ForegroundColor Green

# 2) Dashboard server starts silently at logon (Startup folder; pyw = no console window)
$pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source
if ($pyw) {
    $startup = Join-Path ([Environment]::GetFolderPath("Startup")) "Agents Lab Dashboard.lnk"
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($startup)
    $sc.TargetPath = $pyw
    $sc.Arguments = "-3 -m lab dashboard --no-browser"
    $sc.WorkingDirectory = $root
    $sc.WindowStyle = 7
    $sc.Save()
    Start-Process -FilePath $pyw -ArgumentList "-3 -m lab dashboard --no-browser" -WorkingDirectory $root -WindowStyle Hidden
    Write-Host "Dashboard starts automatically at logon (http://127.0.0.1:8765)." -ForegroundColor Green
} else {
    Write-Host "pyw not found: open the dashboard with open_dashboard.bat" -ForegroundColor Yellow
}

# 3) Desktop shortcut that opens the dashboard
$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "Agents Lab.lnk"
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$sh.TargetPath = "http://127.0.0.1:8765/"
$sh.Save()
Write-Host "Desktop shortcut 'Agents Lab' created." -ForegroundColor Green

# 4) Regenerate the prompts with this folder's real path
Push-Location $root
foreach ($ag in "gemini", "chatgpt", "claude") {
    & py -3 -m lab print-prompt $ag --out "prompts\${ag}_scheduled_prompt.txt" | Out-Null
}
Pop-Location

# 5) Remember where Agents Lab is installed, so the updater finds it
$marker = Join-Path $env:LOCALAPPDATA "AgentsLab"
New-Item -ItemType Directory -Force $marker | Out-Null
Set-Content -Path (Join-Path $marker "install_dir.txt") -Value $root -Encoding utf8
Write-Host "`nDone. Open the dashboard from the desktop shortcut 'Agents Lab'."
Read-Host "Press Enter to close"
