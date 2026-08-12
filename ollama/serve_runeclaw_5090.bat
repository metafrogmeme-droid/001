@echo off
REM ================================================================
REM RUNECLAW v7 8B - serve on the RTX 5090 laptop. NO ADMIN NEEDED:
REM per-session env vars, user-space ollama, localhost port only.
REM
REM Refuses to serve a model whose GGUF metadata does not read 8.0B -
REM the check that caught an 8B shipped under the 3B's name, pointed
REM the other way.
REM ================================================================

set MODEL=pbdes2022/HUMANOID-TRADERS:v7-8b
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

REM [3/4] Model registered? Create from the 5090 Modelfile if not.
ollama list | findstr /i "HUMANOID-TRADERS:v7-8b" >nul
if errorlevel 1 (
    if not exist "%MODELDIR%\Modelfile.5090" (
        echo ERROR: %MODEL% is not registered and %MODELDIR%\Modelfile.5090 is missing.
        echo Run the export chain first:
        echo   python export_model.py --expect-base 8B
        echo   python convert_to_gguf.py
        echo Then copy Modelfile.5090 next to the GGUF in runeclaw-model\.
        pause
        exit /b 1
    )
    echo Creating %MODEL% from Modelfile.5090 ...
    pushd "%MODELDIR%"
    ollama create %MODEL% -f Modelfile.5090
    if errorlevel 1 (
        echo CREATE FAILED - not serving.
        popd
        pause
        exit /b 1
    )
    popd
)

REM [4/4] Identity gate: the GGUF's own metadata must say 8.0B.
ollama show %MODEL% | findstr /C:"8.0B" >nul
if errorlevel 1 (
    echo ERROR: 'ollama show %MODEL%' does not read 8.0B parameters.
    echo The registered model is NOT the v7 8B. Do not serve, do not push.
    echo Re-run the export chain and re-create the model. Full output:
    ollama show %MODEL%
    pause
    exit /b 1
)
echo [OK] %MODEL% verified: parameters 8.0B.
echo.
echo Serving with KEEP_ALIVE=-1, NUM_PARALLEL=1, FLASH_ATTENTION=1.
echo Bot endpoint:  http://localhost:11434/v1
echo Bot model:     %MODEL%
echo.
echo If this exits with 'address already in use': the Ollama tray app owns
echo the port. Quit the tray icon first, then run this again - the tray
echo would serve too, but WITHOUT these env vars.
echo ----------------------------------------------------------------
ollama serve
