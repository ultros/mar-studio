@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1

set "MAR_MODE=start"
if /i "%~1"=="--check" set "MAR_MODE=check"
if /i "%~1"=="--install" set "MAR_MODE=install"
if /i "%~1"=="--skip-install" set "MAR_MODE=skip"
if /i "%~1"=="--help" goto help
set "MAR_PY=.venv\Scripts\python.exe"
set "MAR_PYTHON_TEST=import struct, sys; raise SystemExit(not (sys.version_info >= (3, 12) and struct.calcsize('P') == 8))"

if exist "%MAR_PY%" goto check_python
if "%MAR_MODE%"=="check" goto missing_venv

echo Creating .venv ...
set "MAR_BOOTSTRAP="
py -3.12 -c "%MAR_PYTHON_TEST%" >nul 2>&1 && set "MAR_BOOTSTRAP=py -3.12"
if defined MAR_BOOTSTRAP goto create_venv
py -3 -c "%MAR_PYTHON_TEST%" >nul 2>&1 && set "MAR_BOOTSTRAP=py -3"
if defined MAR_BOOTSTRAP goto create_venv
python -c "%MAR_PYTHON_TEST%" >nul 2>&1 && set "MAR_BOOTSTRAP=python"
if not defined MAR_BOOTSTRAP goto missing_python

:create_venv
%MAR_BOOTSTRAP% -m venv ".venv" || goto failed

:check_python
"%MAR_PY%" -c "%MAR_PYTHON_TEST%" >nul 2>&1 || goto unusable_venv
"%MAR_PY%" "scripts\check_runtime.py"
set "MAR_CHECK=%ERRORLEVEL%"
if "%MAR_MODE%"=="check" goto check_only
if "%MAR_CHECK%"=="0" goto ready
if not "%MAR_CHECK%"=="1" if not "%MAR_CHECK%"=="2" goto failed
if "%MAR_MODE%"=="install" goto install
if "%MAR_MODE%"=="skip" goto skip_unavailable

choice /c IS /n /m "Install dependencies [I] or skip setup and exit [S]? "
if errorlevel 2 goto skipped
if errorlevel 1 goto install
goto skipped

:install
"%MAR_PY%" -m pip --version >nul 2>&1 || "%MAR_PY%" -m ensurepip --default-pip || goto failed
set "MAR_PIP_REPAIR="
if "%MAR_CHECK%"=="2" set "MAR_PIP_REPAIR=--force-reinstall"
"%MAR_PY%" -m pip install %MAR_PIP_REPAIR% --requirement "requirements.lock" || goto failed
"%MAR_PY%" "scripts\check_runtime.py" || goto failed

:ready
if "%MAR_MODE%"=="install" goto success
echo Starting MAR Studio ...
if "%MAR_MODE%"=="skip" goto launch_without_arguments
"%MAR_PY%" "run_studio.py" %*
goto app_exit

:launch_without_arguments
"%MAR_PY%" "run_studio.py"

:app_exit
set "MAR_RESULT=%ERRORLEVEL%"
if not "%MAR_RESULT%"=="0" if "%MAR_MODE%"=="start" pause
goto finish

:help
echo Double-click startup.bat to check dependencies and open MAR Studio.
echo   startup.bat --check         Check the existing .venv and exit.
echo   startup.bat --install       Create .venv and install missing or broken dependencies, then exit.
echo   startup.bat --skip-install  Open MAR Studio if dependencies are ready.
echo   startup.bat "my poster.marproj"  Open a project after checking dependencies.
goto success

:missing_python
echo Install 64-bit Python 3.12 or later, then run startup.bat again. 1>&2
goto failed

:unusable_venv
echo The existing .venv could not run 64-bit Python 3.12 or later. Rename .venv and rerun startup.bat to create a new one. 1>&2
goto failed

:missing_venv
echo .venv is missing. Run startup.bat or startup.bat --install to set it up. 1>&2
goto failed

:skip_unavailable
echo MAR Studio needs the pinned dependencies. Run startup.bat --install and try again. 1>&2
goto failed

:skipped
echo Setup skipped. Run startup.bat again when you are ready to install.
goto success

:failed
set "MAR_RESULT=1"
if "%MAR_MODE%"=="start" pause
goto finish

:check_only
set "MAR_RESULT=%MAR_CHECK%"
goto finish

:success
set "MAR_RESULT=0"

:finish
popd
exit /b %MAR_RESULT%
