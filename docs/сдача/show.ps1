# Показ на защите с ноутбука по HDMI: видео -> живой показ -> слайды 4-6.
# Запуск: двойной щелчок по ПОКАЗ.bat. Проверка без открытия окон: ПОКАЗ.bat -Check (вызывает этот show.ps1)
param([switch]$Check)

$ErrorActionPreference = 'Stop'
$root  = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$video = Join-Path $root 'docs\защита\тизер\Dolce-Pharm_LMS_тизер_черновик.mp4'
$deck  = Join-Path $root 'docs\сдача\Dolce-Pharm_LMS_6_слайдов.pptx'
$url   = 'http://localhost:8000'

function Test-Url($u) {
    # curl.exe есть в Windows 10/11; -k — сертификат платформы самоподписанный
    $code = & curl.exe -sk -o NUL -w '%{http_code}' --max-time 3 $u 2>$null
    return $code -eq '200'
}

$ok = $true
function Report($name, $good, $fix) {
    if ($good) { Write-Host "  [OK]  $name" -ForegroundColor Green }
    else { Write-Host "  [!!]  $name  ->  $fix" -ForegroundColor Red; $script:ok = $false }
}

Write-Host ''
Write-Host 'Проверка перед показом' -ForegroundColor Cyan
Report 'Видео тизера' (Test-Path $video) "нет файла $video"
Report 'Презентация на 6 слайдов' (Test-Path $deck) "нет файла $deck"
Report 'Проигрыватель ffplay' ([bool](Get-Command ffplay -ErrorAction SilentlyContinue)) 'нужен ffmpeg в PATH'
Report 'Платформа http://localhost:8000' (Test-Url $url) 'запустите start.bat'
$hot = [bool](Get-NetIPAddress -IPAddress 192.168.137.1 -ErrorAction SilentlyContinue)
Report 'Хот-спот 192.168.137.1' $hot 'Параметры -> Сеть и Интернет -> Мобильный хот-спот: включить'
if ($hot) { Report 'Адрес для телефона https://192.168.137.1:8443' (Test-Url 'https://192.168.137.1:8443/') 'перезапустите start.bat' }
$screens = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBasicDisplayParams -ErrorAction SilentlyContinue | Where-Object Active).Count
Write-Host "  [i]   Активных мониторов: $screens (проектор по HDMI подключён, если их 2). Режим: Win+P -> Повторяющийся"
Write-Host ''

if ($Check) { if ($ok) { Write-Host 'Всё готово.' -ForegroundColor Green }; return }
if (-not $ok) {
    $a = Read-Host 'Есть проблемы (красные строки). Всё равно продолжить? [д/н]'
    if ($a -notmatch '^[дДyY]') { return }
}

# 1. Платформа в браузере (вход методистом -> «Обучение смены»; F11 — во весь экран, Ctrl + «+» — крупнее)
Start-Process $url

# 2. Презентация: показ слайдов 4-6 (метрики, импакт, пилот) — ждёт за браузером
$pp = New-Object -ComObject PowerPoint.Application
$pres = $pp.Presentations.Open($deck, $true, $false, $true)
$ss = $pres.SlideShowSettings
$ss.RangeType = 2          # ppShowSlideRange
$ss.StartingSlide = 4
$ss.EndingSlide = 6
$null = $ss.Run()

Start-Sleep -Seconds 2
Write-Host 'Браузер и слайды открыты.' -ForegroundColor Green
Write-Host 'Проверьте вход методистом в браузере. Когда выйдете на сцену — нажмите Enter, начнётся видео.' -ForegroundColor Yellow
[void](Read-Host)

# 3. Видео во весь экран, само закрывается в конце
& ffplay -hide_banner -loglevel error -fs -autoexit -window_title 'Dolce-Pharm' $video

# 4. После видео — сразу браузер с платформой
$sh = New-Object -ComObject WScript.Shell
$null = $sh.AppActivate('Dolce-Pharm — обучение')
Write-Host ''
Write-Host 'Дальше: живой показ в браузере. После него Alt+Tab -> слайды 4-6 (стрелка вправо).' -ForegroundColor Cyan
Write-Host 'Видео ещё раз: ffplay -fs -autoexit "<путь>"; Esc — выйти из видео.'
