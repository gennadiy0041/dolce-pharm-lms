@echo off
rem Запуск Dolce Pharm LMS одной командой: база данных, миграции, HTTPS, сервер.
rem Интернет не нужен. Остановка: Ctrl+C в этом окне.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Платформа ещё не установлена. Сначала запустите setup.bat
  pause
  exit /b 1
)
rem Запуск всегда идёт в демонстрационном контуре, даже если в этом окне раньше запускали тесты.
set LMS_ENV=demo
".venv\Scripts\python.exe" run.py %*
if errorlevel 1 pause
endlocal
