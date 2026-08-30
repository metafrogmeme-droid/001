@echo off
REM ================================================================
REM RUNECLAW stay-awake - stops Windows from SLEEPING mid-training.
REM NO ADMIN NEEDED: it asks the Windows power API (the same call
REM video players use), per-process, refreshed every 5 minutes.
REM
REM   - Run this in its OWN window, next to the training window.
REM   - LEAVE THIS WINDOW OPEN for the whole run. Closing it (or
REM     Ctrl+C) restores normal sleep behavior instantly - nothing
REM     is changed system-wide, nothing to undo.
REM   - The DISPLAY may still switch off. That is fine and saves
REM     heat - the machine itself stays awake and training runs on.
REM   - KEEP THE LID OPEN. Closing the lid forces sleep through a
REM     separate policy this script cannot (and should not) touch.
REM ================================================================

set PS1=%TEMP%\runeclaw_stay_awake.ps1
> "%PS1%" echo $sig = '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
>> "%PS1%" echo Add-Type -MemberDefinition $sig -Name PW -Namespace Win32
>> "%PS1%" echo Write-Host 'stay-awake ACTIVE - system sleep is blocked while this window is open.'
>> "%PS1%" echo Write-Host 'Close this window or press Ctrl+C to restore normal sleep.'
>> "%PS1%" echo while ($true) {
REM 2147483649 = ES_CONTINUOUS + ES_SYSTEM_REQUIRED: block system sleep,
REM but let the display turn off (no ES_DISPLAY_REQUIRED bit).
>> "%PS1%" echo   $r = [Win32.PW]::SetThreadExecutionState(2147483649)
>> "%PS1%" echo   if ($r -eq 0) { Write-Host 'WARNING: power request REFUSED - the machine may sleep!' }
>> "%PS1%" echo   Write-Host ('heartbeat ' + (Get-Date -Format 'yyyy-MM-dd HH:mm') + ' - awake')
>> "%PS1%" echo   Start-Sleep -Seconds 300
>> "%PS1%" echo }

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"

echo.
echo stay-awake stopped - normal sleep behavior restored.
pause
