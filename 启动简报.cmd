@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" "%~dp0run.py"
  exit /b 0
)
if exist "C:\Users\Abner\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" (
  start "" "C:\Users\Abner\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" "%~dp0run.py"
  exit /b 0
)
where py >nul 2>nul
if %errorlevel%==0 (
  start "" py -3 "%~dp0run.py"
  exit /b 0
)
echo Python runtime not found. Please use the complete portable edition.
pause
