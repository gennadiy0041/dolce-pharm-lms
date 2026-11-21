@echo off
rem Первичная установка Dolce Pharm LMS: зависимости, сборка клиента, демоданные.
rem Нужен интернет (один раз). После этого платформа запускается без интернета: start.bat
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo [1/4] Окружение Python и зависимости сервера...
if not exist ".venv\Scripts\python.exe" (
  py -3.10 -m venv .venv 2>nul || py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  echo Не найден Python. Установите Python 3.10, 3.11 или 3.12 с python.org и повторите.
  pause
  exit /b 1
)
rem Встроенный PostgreSQL поставляется только для Python 3.9-3.12. С другой версией установка
rem зависимостей не пройдёт, поэтому неподходящее окружение сразу удаляется.
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if (3, 9) <= sys.version_info[:2] <= (3, 12) else 1)"
if errorlevel 1 (
  rmdir /s /q .venv
  echo Найден Python неподходящей версии. Нужен Python 3.10, 3.11 или 3.12: установите его с python.org и повторите.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r backend\requirements.txt
if errorlevel 1 (
  echo Не удалось установить зависимости Python.
  pause
  exit /b 1
)

echo.
echo [2/4] Зависимости и сборка клиента...
pushd frontend
call npm install --no-fund --no-audit
if errorlevel 1 ( popd & echo Не удалось установить зависимости Node.js. & pause & exit /b 1 )
call npm run build
if errorlevel 1 ( popd & echo Сборка клиента не удалась. & pause & exit /b 1 )
popd

echo.
echo [3/4] Учебные материалы...
if not exist "backend\demo\materials\video-sop-check.mp4" ".venv\Scripts\python.exe" backend\demo\make_materials.py

echo.
echo [4/4] База данных и демоданные...
".venv\Scripts\python.exe" run.py seed
if errorlevel 1 ( echo Не удалось подготовить базу данных. & pause & exit /b 1 )

echo.
echo Готово. Запуск платформы: start.bat
endlocal
pause
