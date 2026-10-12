@echo off
rem JARVIS launcher: server.bat [start | restart | stop | status]  (leading - optional; no option = restart)
rem Targets every python process whose command line has jarvis.server: the .venv python
rem is a launcher that spawns the real interpreter as a child, so stopping the parent alone leaves the server.
cd /d "%~dp0"
set "FIND=@(Get-CimInstance Win32_Process | ? { $_.Name -like 'python*' -and $_.CommandLine -like '*jarvis.server*' })"
set "OPT=%~1"
if "%OPT%"=="" set "OPT=restart"
if "%OPT:~0,1%"=="-" set "OPT=%OPT:~1%"
if /i "%OPT%"=="start" goto start
if /i "%OPT%"=="restart" goto restart
if /i "%OPT%"=="stop" goto stop
if /i "%OPT%"=="status" goto status
echo usage: server.bat [start ^| restart ^| stop ^| status]  (no option = restart)
exit /b 1

:status
powershell -NoProfile -Command "$p = %FIND%; if ($p.Count) { 'JARVIS running (pid ' + ($p.ProcessId -join ', ') + ') at http://127.0.0.1:9300/' } else { 'JARVIS is not running' }"
exit /b 0

:stop
call :kill
exit /b 0

:restart
call :kill
rem Replaced a running server: its HUD window reconnects and reloads, so do not open another.
if errorlevel 1 set "JARVIS_NO_BROWSER=1"
:start
rem The server itself detects an already-running instance and just reopens its window.
uv run python -m jarvis.server
exit /b %errorlevel%

:kill
powershell -NoProfile -Command "$p = %FIND%; $p | %% { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; if ($p.Count) { 'JARVIS stopped (' + $p.Count + ' processes)' } else { 'JARVIS was not running' }; exit $p.Count"
exit /b %errorlevel%
