@echo off
rem Автоматические проверки в отдельном контуре "test" (порт 8100): демонстрационные данные не затрагиваются.
rem 1) API: pytest   2) сквозные сценарии в двух сессиях браузера: Playwright (около 10 минут)
rem setlocal: переменная LMS_ENV=test не остаётся в окне после завершения, start.bat снова работает с демо-контуром.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set LMS_ENV=test

echo.
echo [1/2] Проверки API...
pushd backend
"..\.venv\Scripts\python.exe" -m pytest tests -q
popd

echo.
echo [2/2] Сквозные сценарии (Chromium)...
pushd e2e
if not exist node_modules call npm install --no-fund --no-audit
rem Браузер для тестов ставится один раз (нужен интернет); если он уже есть, команда ничего не скачивает.
call npx playwright install chromium
call npx playwright test
popd
echo.
echo Скриншоты прогона: e2e\results\shots    Измерения: e2e\results\measurements.jsonl
endlocal
pause
