@echo off
REM ============================================================================
REM  Agente Fenix v5 - Instalador para Windows (doble clic)
REM ============================================================================
REM  Instala TODO automaticamente. No necesitas permisos de administrador.
REM  Solo haz DOBLE CLIC en este archivo y espera a que diga "TODO LISTO".
REM ============================================================================
setlocal EnableDelayedExpansion

REM --- Ir SIEMPRE a la carpeta donde esta este .bat ---
cd /d "%~dp0"

echo ============================================================
echo    AGENTE FENIX v5 - Instalacion automatica
echo ============================================================
echo.
echo  Carpeta: %~dp0
echo.

REM --- Localizar install.py: aqui mismo, o en una subcarpeta ---
set "INSTALLER="
if exist "%~dp0install.py" (
    set "INSTALLER=%~dp0install.py"
) else (
    echo  Buscando install.py en subcarpetas...
    for /r "%~dp0" %%F in (install.py) do (
        if not defined INSTALLER set "INSTALLER=%%F"
    )
)

if not defined INSTALLER (
    echo.
    echo  [X] No encuentro install.py
    echo.
    echo  Asegurate de haber EXTRAIDO todo el ZIP, y de que este
    echo  archivo .bat este junto a install.py ^(o en una carpeta superior^).
    echo.
    goto :fin
)

echo  Instalador encontrado:
echo    !INSTALLER!
echo.

REM --- Desbloquear archivos (mejor esfuerzo, no requiere admin) ---
powershell -NoProfile -Command "Get-ChildItem -Path '%~dp0' -Recurse -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>nul

REM --- Buscar Python (python o py launcher) ---
set "PYTHON="
where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
    where py >nul 2>nul && set "PYTHON=py"
)

if not defined PYTHON (
    echo.
    echo  [X] FALTA PYTHON
    echo.
    echo  Necesitas instalar Python una sola vez:
    echo    1. Abre:  https://www.python.org/downloads/
    echo    2. Instala Python
    echo    3. IMPORTANTE: marca la casilla "Add Python to PATH"
    echo    4. Vuelve a hacer doble clic en este archivo.
    echo.
    goto :fin
)

echo  Python encontrado: 
%PYTHON% --version
echo.
echo  Instalando el Agente Fenix... (1-3 min la primera vez)
echo  No cierres esta ventana.
echo.

%PYTHON% "!INSTALLER!"
set "RESULT=!ERRORLEVEL!"

echo.
if "!RESULT!"=="0" (
    echo ============================================================
    echo    TODO LISTO  ^|  El Agente Fenix ya esta instalado
    echo ============================================================
    echo.
    echo  Ahora abre opencode o Claude Code y escribe:   /skills
    echo  Veras "agente-fenix". Listo para usar.
    echo  ^(Si opencode ya estaba abierto, cierralo y abrelo de nuevo.^)
) else (
    echo ============================================================
    echo    [X] Hubo un problema durante la instalacion
    echo ============================================================
    echo.
    echo  Revisa los mensajes de arriba y comparte una captura.
)

:fin
echo.
echo ------------------------------------------------------------
echo  Presiona cualquier tecla para cerrar esta ventana...
pause >nul
endlocal
