@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem Launcher for the Ndfbin editor, run from a source checkout.
rem
rem End users should use the packaged EXE instead -- it needs no Python at all.
rem This file is for development, and for machines that already have the
rem interpreter installed.
rem
rem Interpreter search order: project .venv -> the venv that already exists
rem in the older NdfbinDecksEditor project -> system Python.  The second
rem candidate is deliberate: that venv already has PySide6 and zstandard, so
rem there is no reason to install a second copy on the same machine.
rem
rem Nothing here is hard-coded to one computer -- every candidate is an
rem existence test.  Comments stay ASCII on purpose: cmd reads this file in
rem the OEM code page, and non-ASCII comment bytes can swallow the lines
rem that follow them.

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=venv\Scripts\python.exe"
if not defined PY if exist "%USERPROFILE%\NdfbinDecksEditor\.venv\Scripts\python.exe" set "PY=%USERPROFILE%\NdfbinDecksEditor\.venv\Scripts\python.exe"
if not defined PY (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

rem One dependency check instead of two probes: the message below can then
rem name exactly what is missing instead of surfacing as a traceback in a
rem console window that closes before anyone reads it.
%PY% -c "import PySide6, zstandard" >nul 2>&1
if errorlevel 1 goto nodeps

echo Using interpreter: %PY%
%PY% run_weapon_editor.py %*
if errorlevel 1 goto failed
exit /b 0

:nopython
echo.
echo Python was not found.  Python 3.10 or newer is required.
echo Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:nodeps
echo.
echo Found Python (%PY%) but it is missing required packages:
echo     PySide6, zstandard
echo.
echo Run these three lines once, in this folder:
echo.
echo     %PY% -m venv .venv
echo     .venv\Scripts\pip install -r requirements.txt
echo.
pause
exit /b 1

:failed
echo.
echo Startup failed -- the error above should say why.
echo.
pause
exit /b 1