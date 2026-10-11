@echo off
chcp 65001 >nul
title Instalador - Contador de Almas
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
pause
