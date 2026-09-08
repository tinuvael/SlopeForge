@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1

set "PYTHON_COMMAND=py -3.14"
%PYTHON_COMMAND% -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 14) else 'SlopeForge releases require Python 3.14')" || exit /b 1
for /f "usebackq delims=" %%V in (`%PYTHON_COMMAND% -c "from app.config import APP_VERSION; import re; assert re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?', APP_VERSION); print(APP_VERSION)"`) do set "APP_VERSION=%%V"
if not defined APP_VERSION (echo Invalid or empty APP_VERSION.& exit /b 1)

for %%D in (build dist release) do if exist "%%D" rmdir /s /q "%%D"
for %%D in (build dist release) do if exist "%%D" (echo Could not remove %%D.& exit /b 1)

%PYTHON_COMMAND% -m PyInstaller --clean --noconfirm SlopeForge.spec || exit /b 1
if not exist "dist\SlopeForge\SlopeForge.exe" (echo SlopeForge.exe was not built.& exit /b 1)

%PYTHON_COMMAND% -m PyInstaller --clean --noconfirm SlopeForgeUpdater.spec || exit /b 1
if not exist "dist\SlopeForgeUpdater.exe" (echo SlopeForgeUpdater.exe was not built.& exit /b 1)
copy /y "dist\SlopeForgeUpdater.exe" "dist\SlopeForge\SlopeForgeUpdater.exe" >nul || exit /b 1
if not exist "dist\SlopeForge\SlopeForgeUpdater.exe" (echo SlopeForgeUpdater.exe was not added to the release payload.& exit /b 1)

%PYTHON_COMMAND% tools\validate_windows_payload.py "dist\SlopeForge" || exit /b 1

mkdir release || exit /b 1
set "ZIP_PATH=release\SlopeForge-%APP_VERSION%-Windows-x64.zip"
powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; Compress-Archive -LiteralPath 'dist\SlopeForge' -DestinationPath '%ZIP_PATH%' -CompressionLevel Optimal" || exit /b 1

set "ISCC=%ISCC_PATH%"
if not defined ISCC for /f "delims=" %%I in ('where ISCC.exe 2^>nul') do if not defined ISCC set "ISCC=%%I"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (echo Inno Setup 6 ISCC.exe was not found. Set ISCC_PATH or add it to PATH.& exit /b 1)
if not exist "%ISCC%" (echo ISCC.exe does not exist at "%ISCC%".& exit /b 1)

"%ISCC%" /Qp "/DAppVersion=%APP_VERSION%" installer\SlopeForge.iss || exit /b 1
set "SETUP_PATH=release\SlopeForge-%APP_VERSION%-Windows-x64-Setup.exe"
for %%F in ("%ZIP_PATH%" "%SETUP_PATH%") do if not exist %%F (echo Missing release artifact %%F.& exit /b 1)
for %%F in ("%ZIP_PATH%" "%SETUP_PATH%") do if %%~zF LEQ 0 (echo Empty release artifact %%F.& exit /b 1)
echo Built %ZIP_PATH%
echo Built %SETUP_PATH%
exit /b 0
