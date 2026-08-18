@echo off
REM ================================================================
REM RUNECLAW v8 serving verification - run in a SECOND terminal while
REM serve_runeclaw_5090.bat (or the tray) is up. No admin needed.
REM
REM Three verdicts, in order. Each one has burned us when skipped:
REM   1. parameters 8.0B      - the model is what its name claims
REM   2. TRADE IDEA format    - the fine-tune actually took
REM   3. ollama ps 100%% GPU  - it runs where you think it runs
REM ================================================================

set MODEL=pbdes2022/HUMANOID-TRADERS:v8-8b

echo === [1/3] Identity (must read: parameters 8.0B) ===
ollama show %MODEL% | findstr /C:"parameters" /C:"quantization"
echo.

echo === [2/3] Generation (reply MUST carry: TRADE IDEA [TI-...], a Risk ===
echo ===        Check verdict, and 'Status: PENDING' - not generic prose) ===
ollama run %MODEL% --verbose "Analyze BTC/USDT. RSI 28, MACD histogram positive, price at the 61.8%% Fibonacci retracement, ADX 32 with +DI over -DI. What do you see?"
echo.

echo === [3/3] Placement (PROCESSOR must read: 100%% GPU) ===
ollama ps
echo.
echo If all three pass: the endpoint http://localhost:11434/v1 is ready for
echo the bot (.env: RUNECLAW_LLM_BASE_URL + RUNECLAW_LLM_MODEL=%MODEL%,
echo restart the bot), and the eval comes next:
echo   python runeclaw_eval.py --model %MODEL% --output serving_check.json
pause
