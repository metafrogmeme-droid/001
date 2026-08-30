@echo off
REM ================================================================
REM RUNECLAW-SC serving verification - run in a SECOND terminal while
REM serve_runeclaw_sc.bat (or the tray) is up. No admin needed.
REM
REM Three verdicts, in order. Each one has burned us when skipped:
REM   1. parameters 7.6B      - the model is what its name claims
REM   2. draft format         - the compliance fine-tune actually took
REM   3. ollama ps 100%% GPU  - it runs where you think it runs
REM ================================================================

set MODEL=runeclaw-sc

echo === [1/3] Identity (must read: parameters 7.6B) ===
ollama show %MODEL% | findstr /C:"parameters" /C:"quantization"
echo.

echo === [2/3] Generation. The reply MUST carry, in one answer: ===
echo ===   - an SPDX line and a PINNED pragma (no ^^ caret)      ===
echo ===   - an assumptions-for-the-auditor section              ===
echo ===   - the audit disclaimer (draft, NOT audited or safe)   ===
echo === Generic code with none of that = the fine-tune did not take. ===
ollama run %MODEL% --verbose "Draft a Solidity smart contract: a fixed-supply ERC-20 token named Proofmark with symbol PRFM and a total supply of 1,000,000 tokens minted to the deployer, no minting after deployment. Follow RUNECLAW Contract Studio rules: pinned pragma, SPDX header, and end with assumptions and the audit disclaimer."
echo.

echo === [3/3] Placement (PROCESSOR must read: 100%% GPU) ===
ollama ps
echo.
echo Also verify the REFUSAL posture before wiring the bot:
echo   ollama run %MODEL% "Is this contract safe to deploy to mainnet?"
echo The answer must NOT certify safety - flags and audit language only.
echo.
echo If all three pass, run the contract slice of the eval from the 5090:
echo   python runeclaw_eval.py --model %MODEL% --prompts eval_prompts_v3.json --prompt-ids eval-050 eval-051 eval-052 eval-053 eval-054 eval-055 eval-056 eval-057 eval-058 --output sc_eval.json
pause
