@echo off
REM ================================================================
REM RUNECLAW stay-awake v2 - FALLBACK for machines where the power-
REM API approach (stay_awake.bat) is blocked or ignored.
REM
REM Method: presses the harmless F15 key every 59 seconds via
REM Windows Script Host (no PowerShell, no admin). Windows counts it
REM as real user input, which resets EVERY idle timer - sleep,
REM display, and Modern Standby alike. F15 does not exist on real
REM keyboards, so no application reacts to it.
REM
REM   - LEAVE THIS WINDOW OPEN for the whole training run.
REM   - Closing this window (or Ctrl+C) stops it instantly.
REM   - The display will STAY ON (input resets the display timer
REM     too) - that is the price of the reliable method.
REM   - KEEP THE LID OPEN. Lid-close forces sleep regardless.
REM ================================================================

set VBS=%TEMP%\runeclaw_stay_awake.vbs
> "%VBS%" echo Set ws = CreateObject("WScript.Shell")
>> "%VBS%" echo WScript.Echo "stay-awake v2 ACTIVE - pressing F15 every 59s. Close this window to stop."
>> "%VBS%" echo Do
>> "%VBS%" echo   ws.SendKeys "{F15}"
>> "%VBS%" echo   WScript.Echo "heartbeat " ^& Now ^& " - awake"
>> "%VBS%" echo   WScript.Sleep 59000
>> "%VBS%" echo Loop

cscript //nologo "%VBS%"

echo.
echo stay-awake v2 stopped - normal idle behavior restored.
pause
