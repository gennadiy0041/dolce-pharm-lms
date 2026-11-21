@echo off
rem Переключение сборки приложения, которую отдаёт сервер (папка frontend\dist).
rem   switch-build.bat 1  - сборка 1: проверена на телефоне POCO 20.11.2026 (прогоны 1 и 2), прежнее оформление
rem   switch-build.bat 2  - сборка 2: фирменный стиль Dolce-Pharm, сводка "Обучение смены", путь задания
rem Данные (база, файлы, результаты) не меняются: переключается только интерфейс. Сервер перезапускать не нужно.
rem Телефон получает другую сборку сам: откройте приложение при включённой сети, закройте и откройте ещё раз.
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="1" set SRC=releases\v1-run2-verified\frontend-dist
if "%~1"=="2" set SRC=releases\v2-brand\frontend-dist
if not defined SRC (
  echo Укажите номер сборки: switch-build.bat 1   или   switch-build.bat 2
  echo.
  echo Сейчас сервер отдаёт:
  if exist frontend\dist\build.txt type frontend\dist\build.txt
  pause
  exit /b 1
)
if not exist "%SRC%\index.html" (
  echo Не найдена папка %SRC%
  pause
  exit /b 1
)

robocopy "%SRC%" frontend\dist /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo Не удалось скопировать сборку. Закройте программы, которые держат файлы в frontend\dist, и повторите.
  pause
  exit /b 1
)
echo.
echo Сейчас сервер отдаёт:
type frontend\dist\build.txt
echo.
echo Телефон: откройте приложение при включённой сети, смахните его и откройте ещё раз.
endlocal
pause
