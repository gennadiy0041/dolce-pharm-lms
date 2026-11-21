@echo off
rem Возвращает демонстрационный контур в исходное состояние: стирает все попытки, результаты
rem и созданные курсы, заново загружает демоданные со свежими сроками. Сервер перед этим нужно
rem остановить (Ctrl+C в его окне).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set LMS_ENV=demo
echo Все данные демонстрационного контура будут стёрты и загружены заново.
set /p answer=Продолжить? Введите Y и нажмите Enter:
if /i not "%answer%"=="Y" (
  echo Отменено.
  pause
  exit /b 0
)
".venv\Scripts\python.exe" run.py reset-demo
endlocal
pause
