@echo off
REM Roda o contador direto desta pasta, sem instalar (para quem vai mexer no codigo)
cd /d "%~dp0"
where pyw >/dev/null 2>/dev/null && (start "" pyw -3 contador_almas.py & exit /b 0)
where pythonw >/dev/null 2>/dev/null && (start "" pythonw contador_almas.py & exit /b 0)
python contador_almas.py
pause
