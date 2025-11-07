@echo off
REM Lanza Google Chrome en modo Incógnito y habilita DevTools (CDP) en un puerto.
REM Uso: chrome_incognito.cmd [PUERTO] [URL]

setlocal
set PORT=%1
if "%PORT%"=="" set PORT=9223
set URL=%2
if "%URL%"=="" set URL=https://www.google.com

REM Detectar chrome.exe en rutas comunes
set CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME_EXE%" set CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if not exist "%CHROME_EXE%" set CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe

REM Directorio de perfil temporal/controlado en el repo
set PROFILE_DIR=%~dp0chrome_profile
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

echo [chrome_incognito.cmd] Ejecutando: "%CHROME_EXE%" --remote-debugging-port=%PORT% --incognito --new-window --no-first-run --no-default-browser-check --user-data-dir="%PROFILE_DIR%" --disable-extensions --disable-background-networking --disable-sync --disable-component-update --blink-settings=imagesEnabled=false %URL%
start "" "%CHROME_EXE%" --remote-debugging-port=%PORT% --incognito --new-window --no-first-run --no-default-browser-check --user-data-dir="%PROFILE_DIR%" --disable-extensions --disable-background-networking --disable-sync --disable-component-update --blink-settings=imagesEnabled=false %URL%
endlocal