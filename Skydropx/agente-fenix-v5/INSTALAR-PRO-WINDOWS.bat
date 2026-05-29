@echo off
REM ============================================================================
REM  Agente Fenix v5 - Instalador PRO para Windows (cargas pesadas)
REM ============================================================================
REM  Instala capacidades avanzadas (mega-corridas >10,000 leads, OSINT profundo,
REM  analytics OLAP, colas). Usalo SOLO si ya instalaste el basico y necesitas mas.
REM  Se auto-evalua y auto-repara. Haz DOBLE CLIC y espera.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo    AGENTE FENIX v5 - Instalador PRO (cargas pesadas)
echo ============================================================
echo.

REM --- Localizar install-pro.py (aqui o en subcarpeta) ---
set "INSTALLER="
if exist "%~dp0install-pro.py" (
    set "INSTALLER=%~dp0install-pro.py"
) else (
    echo  Buscando install-pro.py en subcarpetas...
    for /r "%~dp0" %%F in (install-pro.py) do (
        if not defined INSTALLER set "INSTALLER=%%F"
    )
)

if not defined INSTALLER (
    echo  [X] No encuentro install-pro.py. Extrae todo el ZIP del proyecto.
    echo.
    goto :fin
)

REM --- Buscar Python ---
set "PYTHON="
where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON ( where py >nul 2>nul && set "PYTHON=py" )

if not defined PYTHON (
    echo  [X] FALTA PYTHON. Instalalo desde https://www.python.org/downloads/
    echo      ^(marca "Add Python to PATH"^) y vuelve a intentar.
    echo.
    goto :fin
)

echo  Instalando capacidades PRO... (puede tardar varios minutos)
echo  Algunos paquetes pesados pueden omitirse sin afectar el uso normal.
echo.

%PYTHON% "!INSTALLER!"

:fin
echo.
echo ------------------------------------------------------------
echo  Presiona cualquier tecla para cerrar esta ventana...
pause >nul
endlocal
