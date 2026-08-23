@echo off
REM ================================================================
REM RUNECLAW serving stack - launches ALL THREE processes the bot
REM depends on, each in its own labeled window. NO ADMIN NEEDED.
REM
REM   [1] ollama serve      (model, port 11434, pinned in VRAM)
REM   [2] auth proxy        (Bearer gate, port 11435)
REM   [3] cloudflared       (HTTP/2 tunnel to the proxy)
REM   [4] stay-awake        (blocks system sleep, works when locked)
REM
REM After a reboot or crash: double-click THIS file once. Four
REM windows open; all four must STAY OPEN. The tunnel window prints
REM the public URL - if it changed (quick tunnels change URL every
REM restart), the bot's RUNECLAW_LLM_BASE_URL must be updated.
REM
REM The proxy dying silently is what took the stack down on 18-08:
REM every bot call 502'd and the bot fell back to rules with no
REM visible error. Labeled windows make a dead process obvious.
REM ================================================================

if not defined RUNECLAW_PROXY_TOKEN (
    echo [FATAL] RUNECLAW_PROXY_TOKEN is not set - refusing to start.
    echo         Set it for this machine with:  setx RUNECLAW_PROXY_TOKEN your-token
    echo         then open a NEW terminal and re-run this launcher.
    exit /b 1
)
set TOKEN=%RUNECLAW_PROXY_TOKEN%
set MODEL=pbdes2022/humanoid-traders:v10-8b
cd /d "%~dp0"

echo [1/4] Starting Ollama (KEEP_ALIVE=-1, NUM_PARALLEL=4, FLASH_ATTENTION=1, KV=q8_0, CTX=8192)...
start "RUNECLAW 1of4 - OLLAMA (keep open)" cmd /k ^
 "set OLLAMA_KEEP_ALIVE=-1&& set OLLAMA_NUM_PARALLEL=4&& set OLLAMA_FLASH_ATTENTION=1&& set OLLAMA_KV_CACHE_TYPE=q8_0&& set OLLAMA_CONTEXT_LENGTH=8192&& ollama serve"

timeout /t 5 /nobreak >nul

echo [2/4] Starting auth proxy on 127.0.0.1:11435...
start "RUNECLAW 2of4 - AUTH PROXY (keep open)" cmd /k ^
 "set RUNECLAW_PROXY_TOKEN=%TOKEN%&& python ollama_auth_proxy.py"

timeout /t 3 /nobreak >nul

echo [3/4] Starting cloudflared HTTP/2 tunnel...
start "RUNECLAW 3of4 - TUNNEL (keep open)" cmd /k ^
 "cloudflared.exe tunnel --protocol http2 --url http://127.0.0.1:11435"

timeout /t 2 /nobreak >nul

echo [4/4] Starting stay-awake (SetThreadExecutionState - works while locked)...
start "RUNECLAW 4of4 - STAY AWAKE (keep open)" cmd /k stay_awake.bat

echo.
echo All four launched. Check each window:
echo   1of4 OLLAMA:  no 'address already in use' (quit the tray app if so)
echo   2of4 PROXY:   'RUNECLAW auth gate: http://127.0.0.1:11435'
echo   3of4 TUNNEL:  the https://....trycloudflare.com URL in the box
echo   4of4 AWAKE:   'system sleep blocked' heartbeat lines
echo.
echo Waiting for the stack to ANSWER (model load + warm-up, up to ~2 min)...
echo A window being open is not the stack being up - only a completion
echo coming back through the auth proxy proves ollama AND the token gate.
set /a _tries=0
:warmloop
set /a _tries+=1
curl -s -m 15 -H "Authorization: Bearer %TOKEN%" -H "Content-Type: application/json" -d "{\"model\":\"%MODEL%\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_tokens\":8}" http://127.0.0.1:11435/v1/chat/completions | findstr /C:"choices" >nul
if not errorlevel 1 goto warmok
if %_tries% geq 40 goto warmfail
timeout /t 3 /nobreak >nul
goto warmloop
:warmfail
echo.
echo *** NOT READY: no completion after ~2 minutes. ***
echo Check window 1of4 (ollama) and 2of4 (proxy) for errors. Do NOT point
echo the bot at this stack until this script prints READY.
goto warmend
:warmok
echo.
echo READY: %MODEL% answered through the auth proxy.
echo The tunnel URL in window 3of4 is now serving a live model.
:warmend
echo.
echo If the tunnel URL differs from the one in the bot's .env, update
echo RUNECLAW_LLM_BASE_URL on the bot and restart it.
echo.
pause
