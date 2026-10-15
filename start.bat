@echo off
chcp 65001 >nul 2>&1
title Jiangxi Software Vocational University - Work-Study System

echo ============================================
echo   Jiangxi Software Work-Study System
echo   Blockchain: Geth Dev Chain (chainId auto-detect)
echo ============================================
echo.

cd /d "%~dp0"

echo [0/4] Checking blockchain node on 8545...
powershell -NoProfile -Command "$up = Test-NetConnection -ComputerName 127.0.0.1 -Port 8545 -WarningAction SilentlyContinue -InformationLevel Quiet; if (-not $up) { Start-Process -FilePath 'D:\Campus\geth-node\old startgeth.bat' -WorkingDirectory 'D:\Campus\geth-node'; Write-Output '  Geth node not running, starting automatically...' } else { Write-Output '  Blockchain node (geth) already running.' }"
set /a CHAIN_TRIES=0
:WAIT_CHAIN
set /a CHAIN_TRIES+=1
powershell -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 8545 -WarningAction SilentlyContinue -InformationLevel Quiet) { exit 0 } else { exit 1 }"
if not errorlevel 1 goto CHAIN_OK
if %CHAIN_TRIES% lss 20 goto CHAIN_MORE
goto CHAIN_OK
:CHAIN_MORE
timeout /t 2 /nobreak >nul
goto WAIT_CHAIN
:CHAIN_OK
echo       Blockchain node ready.

echo [1/4] Database reset (AUTO: clearing demo data - no prompt)
echo       NOTE: clearing wipes jobs / employments / clock_ins / settlements / hour_limits (users are kept).
rem auto-clear enabled (no prompt needed)
rem auto-clear enabled (no prompt needed)
if 1==1 (
    call venv\Scripts\activate.bat && python -c "from main import engine; from sqlalchemy import text; conn = engine.connect(); conn.execute(text('DELETE FROM settlements')); conn.execute(text('DELETE FROM clock_ins')); conn.execute(text('DELETE FROM employments')); conn.execute(text('DELETE FROM hour_limits')); conn.execute(text('DELETE FROM rewards_penalties')); conn.execute(text('DELETE FROM fines_appeals')); conn.execute(text('DELETE FROM bans')); conn.execute(text('DELETE FROM student_accounts')); conn.execute(text('DELETE FROM jobs')); conn.commit(); print('  Business tables cleared (users kept)')"
) else (
    echo       Keeping existing data.
)
echo.

echo [2/4] Deploying smart contract...
call npx hardhat run scripts/deploy.js --network localhost
if errorlevel 1 (
    echo [ERROR] Contract deployment failed! Is geth running on port 8545?
    pause
    exit /b 1
)
echo.

echo [3/4] Starting backend server...
echo       Checking port 8000 for stale backend...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'uvicorn|spawn_main' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Seconds 1; Write-Output '  Cleared stale backend processes (port 8000).'"
start "Backend" cmd /k "cd /d "%~dp0" && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
echo       Waiting 4 seconds...
set /a API_TRIES=0
:WAIT_API
set /a API_TRIES+=1
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/public_jobs' -TimeoutSec 2; if ($r -is [array]) { exit 0 } else { exit 1 } } catch { exit 1 }"
if not errorlevel 1 goto API_OK
if %API_TRIES% lss 40 goto WAIT_MORE
goto API_OK
:WAIT_MORE
timeout /t 2 /nobreak >nul
goto WAIT_API
:API_OK
echo       API is up (demo data seeded).

echo.
echo [4/4] Opening browser...
start http://127.0.0.1:8000

echo.
echo ============================================
echo   System started!
echo   Frontend:  http://127.0.0.1:8000
echo   API docs:  http://127.0.0.1:8000/docs
echo   Blockchain: http://127.0.0.1:8545 (Geth)
echo   Chain ID:  auto (follow node eth_chainId, geth --dev = 1337)
echo ============================================
pause
