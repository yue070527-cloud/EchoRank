@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONPATH=%CD%\src"

echo Updating EchoRank...
python -m echorank --database "data\echorank-live.db" update
if errorlevel 1 goto :failed

powershell -NoProfile -Command "$processes = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'python.*-m echorank.*serve.*8765' }); foreach ($process in $processes) { Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500"
start "EchoRank Server" /min python -m echorank --database "data\echorank-live.db" serve --frontend "%CD%\frontend" --port 8765
powershell -NoProfile -Command "$ready = $false; 1..20 | ForEach-Object { if (-not $ready) { try { $response = Invoke-WebRequest 'http://127.0.0.1:8765/api/admin/netease/search?query=EchoRank' -UseBasicParsing -TimeoutSec 3; $ready = $response.StatusCode -eq 200 } catch { if ($_.Exception.Response.StatusCode.value__ -eq 502) { $ready = $true } }; if (-not $ready) { Start-Sleep -Milliseconds 250 } } }; if (-not $ready) { exit 1 }"
if errorlevel 1 goto :server_failed

start "" "http://localhost:8765"
echo EchoRank is ready. Opening the browser...
powershell -NoProfile -Command "Start-Sleep -Seconds 3"
popd
exit /b 0

:server_failed
echo.
echo EchoRank server failed to start. Port 8765 may be used by another program.
echo.
pause
popd
exit /b 1

:failed
echo.
echo EchoRank update failed. See the message above.
echo.
pause
popd
exit /b 1
