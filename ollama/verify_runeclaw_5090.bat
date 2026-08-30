@echo off
REM ================================================================
REM RUNECLAW serving verification - run in a SECOND terminal while
REM serve_stack.bat (or the tray) is up. No admin needed.
REM
REM Four verdicts, in order. Each one has burned us when skipped:
REM   1. parameters 8.0B      - the model is what its name claims
REM   2. TRADE IDEA format    - the fine-tune actually took
REM   3. ollama ps 100%% GPU  - it runs where you think it runs
REM   4. CONTEXT 8192         - the tuned profile survived the restart
REM
REM The model defaults to whatever is DEPLOYED, and takes an override:
REM   verify_runeclaw_5090.bat pbdes2022/humanoid-traders:v8-8b
REM This file used to hardcode the v8 tag and kept being run against a
REM model the bot had stopped using - a check that verifies the wrong
REM subject passes while production is broken.
REM ================================================================

set MODEL=%~1
if "%MODEL%"=="" set MODEL=pbdes2022/humanoid-traders:v10-8b

echo Verifying: %MODEL%
echo.

echo === [1/4] Identity (must read: parameters 8.0B) ===
ollama show %MODEL% | findstr /C:"parameters" /C:"quantization"
echo.

echo === [2/4] Generation (reply MUST carry: TRADE IDEA [TI-...], a Risk ===
echo ===        Check verdict, and 'Status: PENDING' - not generic prose) ===
ollama run %MODEL% --verbose "Analyze BTC/USDT. RSI 28, MACD histogram positive, price at the 61.8%% Fibonacci retracement, ADX 32 with +DI over -DI. What do you see?"
echo.

echo === [3/4] Placement (PROCESSOR must read: 100%% GPU) ===
echo === [4/4] Profile   (CONTEXT must read: 8192, not 4096)  ===
ollama ps
echo.
echo A CONTEXT of 4096 means the 8K variant was replaced (an `ollama pull`
echo restores the registry copy) - rebuild it before pointing the bot here:
echo   ollama create v10tmp -f Modelfile.v10-8k
echo   ollama cp v10tmp %MODEL%  ^&^&  ollama rm v10tmp  ^&^&  ollama stop %MODEL%
echo.
echo If all four pass: http://localhost:11434/v1 is ready for the bot
echo (.env: RUNECLAW_LLM_BASE_URL + RUNECLAW_LLM_MODEL=%MODEL%, restart
echo the bot), and the eval comes next:
echo   python runeclaw_eval.py --model %MODEL% --output serving_check.json
pause
