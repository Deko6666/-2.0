@echo off
chcp 65001 >nul 2>&1
title Jiangxi Software Vocational University - Work-Study System

echo ============================================
echo   Jiangxi Software Work-Study System
echo   Blockchain: Geth Dev Chain (887766)
echo ============================================
echo.

cd /d "%~dp0"

echo [1/4] Database check
echo       NOTE: clearing wipes jobs / employments / clock_ins / settlements (users are kept).
set "CLEAR_DB=N"
set /p CLEAR_DB=  Clear business tables? [Y/N, default N]:
if /i "%CLEAR_DB%"=="Y" (
    call venv\Scripts\activate.bat && python -c "from main import engine; from sqlalchemy import text; conn = engine.connect(); conn.execute(text('DELETE FROM settlements')); conn.execute(text('DELETE FROM clock_ins')); conn.execute(text('DELETE FROM employments')); conn.execute(text('DELETE FROM jobs')); conn.commit(); print('  Business tables cleared (users kept)')"
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
start "Backend" cmd /k "cd /d "%~dp0" && call venv\Scripts\activate.bat && python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload"
echo       Waiting 4 seconds...
timeout /t 4 /nobreak >nul

echo.
echo [4/4] Opening browser...
start http://127.0.0.1:8000

echo.
echo ============================================
echo   System started!
echo   Frontend:  http://127.0.0.1:8000
echo   API docs:  http://127.0.0.1:8000/docs
echo   Blockchain: http://127.0.0.1:8545 (Geth)
echo   Chain ID:  887766 (0xD8BD6)
echo ============================================
pause
