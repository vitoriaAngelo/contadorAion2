@echo off
REM Roda o contador direto desta pasta, sem instalar (para quem vai mexer no codigo)
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 contador_almas.py & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw contador_almas.py & exit /b 0)
python contador_almas.py
pause
