@echo off
rem fath.cmd -- run `fath` straight from the clone, with no setup.
rem
rem   .\fath              (PowerShell, from the clone: opens the dashboard)
rem   .\fath web          (PowerShell, from the clone)
rem   fath web            (cmd, from the clone)
rem   C:\path\to\clone\fath web   (anywhere)
rem
rem scripts\install-shim.ps1 makes a bare `fath` work in every PowerShell
rem session, with tab completion; this file is for before that, or without it.
rem Python resolution matches scripts\fath-shim.ps1: `py -3` first, because a
rem bare `python` on a fresh Windows box is often the Microsoft Store stub.

setlocal
set "FATHOM_HELPER_DIR=%~dp0"
if "%FATHOM_HELPER_DIR:~-1%"=="\" set "FATHOM_HELPER_DIR=%FATHOM_HELPER_DIR:~0,-1%"
set "FATH_ENTRY=%FATHOM_HELPER_DIR%\fath\main.py"

rem No arguments means "get me going": the dashboard, not the help text. The
rem shim's bare `fath` still prints help; this file is the front door.
set "FATH_DEFAULT="
if "%~1"=="" set "FATH_DEFAULT=web"

set "FATH_CHECK=import sys; sys.exit(sys.version_info[:2] < (3, 10))"
py -3 -c "%FATH_CHECK%" >nul 2>&1 && (py -3 "%FATH_ENTRY%" %FATH_DEFAULT% %* & goto :done)
python -c "%FATH_CHECK%" >nul 2>&1 && (python "%FATH_ENTRY%" %FATH_DEFAULT% %* & goto :done)
python3 -c "%FATH_CHECK%" >nul 2>&1 && (python3 "%FATH_ENTRY%" %FATH_DEFAULT% %* & goto :done)

echo fath: Python 3.10+ not found; it is required. Install it, then rerun. 1>&2
exit /b 1

:done
exit /b %ERRORLEVEL%
