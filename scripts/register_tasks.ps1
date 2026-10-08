# Register the EarthScape health-check and backup scheduled tasks for the current user
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
$manage = Join-Path $root 'src\app\manage.py'
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$health = New-ScheduledTaskAction -Execute $py -Argument "`"$manage`" health-check" -WorkingDirectory $root
$backup = New-ScheduledTaskAction -Execute $py -Argument "`"$manage`" backup --keep 7" -WorkingDirectory $root
$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15) -RepetitionDuration (New-TimeSpan -Days 3650)
$daily = New-ScheduledTaskTrigger -Daily -At 2:00AM
Register-ScheduledTask -TaskName 'EarthScape health check' -Action $health -Trigger $every15 -Settings $set -Description 'EarthScape local health check (logs\health.jsonl)' -Force | Out-Null
Register-ScheduledTask -TaskName 'EarthScape backup' -Action $backup -Trigger $daily -Settings $set -Description 'EarthScape MongoDB application-data backup, keeps newest 7' -Force | Out-Null
Get-ScheduledTask -TaskName 'EarthScape*' | Format-Table TaskName, State
