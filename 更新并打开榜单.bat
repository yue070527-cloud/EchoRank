@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONPATH=%CD%\src"

echo Updating EchoRank...
python -m echorank --database "data\echorank-live.db" update
if errorlevel 1 goto :failed

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 start "EchoRank Server" /min python -m http.server 8765 --directory "%CD%\frontend"

start "" "http://localhost:8765"
echo EchoRank is ready. Opening the browser...
powershell -NoProfile -Command "Start-Sleep -Seconds 3"
popd
exit /b 0

:failed
echo.
echo EchoRank update failed. See the message above.
echo.
pause
popd
exit /b 1
