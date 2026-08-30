@echo off
REM ================================================================
REM RUNECLAW-SC (smart-contract 7B) - serve on the SECOND 8GB box.
REM NO ADMIN NEEDED: per-session env vars, user-space ollama,
REM localhost port only. The trade LLM runs on the OTHER box; this
REM one serves only Contract Studio.
REM
REM Identity gate: refuses to serve unless the GGUF metadata reads
REM 7.6B - the wrong-model-under-the-right-name failure has shipped
REM four times from this pipeline; the gate is not optional.
REM ================================================================

set MODEL=runeclaw-sc
set MODELDIR=%~dp0runeclaw-model

REM [1/4] ollama present?
where ollama >nul 2>&1
if errorlevel 1 (
    echo ERROR: ollama not found on PATH.
    echo Install the per-user Windows build - no admin needed:
    echo   https://ollama.com/download
    pause
    exit /b 1
)

REM [2/4] Env for THIS serving session only (user-level, no admin, no setx)
set OLLAMA_KEEP_ALIVE=-1
set OLLAMA_NUM_PARALLEL=1
set OLLAMA_FLASH_ATTENTION=1

REM [3/4] Model registered? Create from Modelfile.sc if not.
ollama list | findstr /i "runeclaw-sc" >nul
if errorlevel 1 (
    if not exist "%MODELDIR%\Modelfile.sc" (
        echo ERROR: %MODEL% is not registered and %MODELDIR%\Modelfile.sc is missing.
        echo Run the SC export chain first on the 5090:
        echo   python export_model.py --expect-base Qwen2.5-Coder-7B
        echo   ^(then the official convert_hf_to_gguf.py + llama-quantize Q4_K_M^)
        echo Then copy the GGUF + Modelfile.sc into runeclaw-model\ on this box.
        pause
        exit /b 1
    )
    echo Creating %MODEL% from Modelfile.sc ...
    pushd "%MODELDIR%"
    ollama create %MODEL% -f Modelfile.sc
    if errorlevel 1 (
        echo CREATE FAILED - not serving.
        popd
        pause
        exit /b 1
    )
    popd
)

REM [4/4] Identity gate: the GGUF's own metadata must say 7.6B (Qwen2.5 7B).
ollama show %MODEL% | findstr /C:"7.6B" >nul
if errorlevel 1 (
    echo ERROR: 'ollama show %MODEL%' does not read 7.6B parameters.
    echo The registered model is NOT the Qwen2.5-Coder-7B SC fine-tune.
    echo Do not serve. Re-run the export chain. Full output:
    ollama show %MODEL%
    pause
    exit /b 1
)
echo [OK] %MODEL% verified: parameters 7.6B.
echo.
echo Serving with KEEP_ALIVE=-1, NUM_PARALLEL=1, FLASH_ATTENTION=1.
echo Local endpoint:  http://localhost:11434/v1
echo Bot env (via the auth proxy + this box's tunnel):
echo   RUNECLAW_SC_BASE_URL=https://^<this-box-tunnel^>/v1
echo   RUNECLAW_SC_MODEL=%MODEL%
echo   RUNECLAW_SC_API_KEY=^<this box's proxy token^>
echo.
echo If this exits with 'address already in use': the Ollama tray app owns
echo the port. Quit the tray icon first, then run this again - the tray
echo would serve too, but WITHOUT these env vars.
echo ----------------------------------------------------------------
ollama serve
