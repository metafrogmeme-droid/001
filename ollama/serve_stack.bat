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

set TOKEN=os1OFozqYEe1uXQd3o6u6rRllqiTjuLv
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
echo If the tunnel URL differs from the one in the bot's .env, update
echo RUNECLAW_LLM_BASE_URL on the bot and restart it.
echo.
pause
