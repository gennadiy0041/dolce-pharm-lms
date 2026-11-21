@echo off
rem Учётная запись для защиты: показывает неиспользованную (без назначений и попыток) или создаёт
rem следующую. Ничего не стирает — результаты репетиций остаются. Сервер останавливать не нужно.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set LMS_ENV=demo
".venv\Scripts\python.exe" run.py stage-account
endlocal
pause
