# Настоящие файлы Word, Excel и PowerPoint с таблицами и диаграммами — для проверки PDF-копий
# и офлайн-просмотра. Файлы создаёт сам Microsoft Office (как у методиста), а не библиотека.
#
#   powershell -ExecutionPolicy Bypass -File tools\make_office_samples.ps1 [папка]
param([string]$OutDir = (Join-Path $PSScriptRoot '..\e2e\fixtures\office-real'), [string[]]$Parts = @('xlsx', 'pptx'))
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
New-Item -ItemType Directory -Force $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path

function Fill-ChartData($chart, $rows) {
  # данные встроенной диаграммы Word/PowerPoint лежат в маленькой книге Excel
  $wb = $null
  for ($i = 0; $i -lt 20 -and $null -eq $wb; $i++) {
    try { $chart.ChartData.ActivateChartDataWindow() } catch { try { $chart.ChartData.Activate() } catch {} }
    Start-Sleep -Milliseconds 500
    try { $wb = $chart.ChartData.Workbook } catch { $wb = $null }
  }
  if ($null -eq $wb) { 'данные диаграммы недоступны — оставлены образцовые'; return }
  $ws = $wb.Worksheets.Item(1)
  $ws.Cells.Clear() | Out-Null
  for ($r = 0; $r -lt $rows.Count; $r++) {
    # через Formula: так Excel сам распознаёт числа (Value2 из Word принимает только текст)
    for ($c = 0; $c -lt $rows[$r].Count; $c++) { $ws.Cells.Item($r + 1, $c + 1).Formula = [string]$rows[$r][$c] }
  }
  $last = $ws.Cells.Item($rows.Count, $rows[0].Count).Address($false, $false)
  $chart.SetSourceData("='" + $ws.Name + "'!A1:" + $last)
  $wb.Application.Quit()
}

$data = @(
  @('Участок', 'Отклонения, шт.', 'Устранено, шт.'),
  @('Таблетирование', 7, 6),
  @('Фасовка', 4, 4),
  @('Упаковка', 9, 7),
  @('Склад', 2, 2)
)

# ---------- Word ----------
# На ноутбуке разработки сохранение из Word по скрипту зависает (скрытый вопрос Word), поэтому
# документ Word с таблицей и диаграммой делает tools\make_office_samples.py; этот раздел — по запросу -Parts docx.
if ($Parts -contains 'docx') {
$word = New-Object -ComObject Word.Application
$word.Visible = $false; $word.DisplayAlerts = 0
try {
  $doc = $word.Documents.Add()
  $sel = $word.Selection
  $sel.Style = $doc.Styles.Item(-2)            # Заголовок 1
  'word: started'
  $sel.TypeText('СОП-ОК-021. Контроль отклонений на участках'); $sel.TypeParagraph()
  $sel.Style = $doc.Styles.Item(-1)            # Обычный
  $sel.TypeText('Документ проверяет перенос таблиц и диаграмм в PDF-копию для чтения с телефона без сети. ' +
    'Ниже — сводная таблица отклонений за ноябрь и диаграмма по участкам.'); $sel.TypeParagraph()
  $sel.Style = $doc.Styles.Item(-3)            # Заголовок 2
  $sel.TypeText('1. Сводная таблица'); $sel.TypeParagraph()
  $sel.Style = $doc.Styles.Item(-1)
  $table = $doc.Tables.Add($sel.Range, $data.Count, 3)
  $table.Borders.Enable = $true
  for ($r = 0; $r -lt $data.Count; $r++) { for ($c = 0; $c -lt 3; $c++) { $table.Cell($r + 1, $c + 1).Range.Text = [string]$data[$r][$c] } }
  $table.Rows.Item(1).Range.Font.Bold = $true
  $table.Rows.Item(1).Shading.BackgroundPatternColor = 14277081
  $sel.EndKey(6) | Out-Null
  $sel.TypeParagraph()
  $sel.Style = $doc.Styles.Item(-3)
  $sel.TypeText('2. Диаграмма отклонений по участкам'); $sel.TypeParagraph()
  $sel.Style = $doc.Styles.Item(-1)
  'word: table done'
  $shape = $doc.InlineShapes.AddChart2(-1, 51, $sel.Range)      # гистограмма с группировкой
  # данные диаграммы — образцовые данные Office: правка встроенной книги из скрипта ненадёжна
  $shape.Chart.HasTitle = $true
  $shape.Chart.ChartTitle.Text = 'Отклонения и устранение по участкам'
  $sel.EndKey(6) | Out-Null
  $sel.TypeParagraph()
  $sel.TypeText('3. Ответственный за контроль — мастер участка. Пересмотр СОП — ежегодно.')
  'word: chart done'
  $doc.SaveAs2((Join-Path $OutDir 'СОП-ОК-021 Контроль отклонений.docx'), 16)
  $doc.Close()
} finally { $word.Quit() }
'docx ok'
}

# ---------- Excel ----------
if ($Parts -contains 'xlsx') {
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false; $xl.DisplayAlerts = $false
try {
  $wb = $xl.Workbooks.Add()
  $ws = $wb.Worksheets.Item(1)
  $ws.Name = 'Журнал температур'
  $head = @('Дата', 'Склад №1, °C', 'Склад №2, °C', 'Влажность, %', 'Норма', 'Ответственный', 'Подпись', 'Примечание')
  for ($c = 0; $c -lt $head.Count; $c++) { $ws.Cells.Item(1, $c + 1).Value2 = $head[$c] }
  $names = @('Ахметова Г. С.', 'Ли С. О.', 'Ким А. В.')
  for ($d = 1; $d -le 30; $d++) {
    $r = $d + 1
    $ws.Cells.Item($r, 1).Value2 = "$('{0:00}' -f $d).11.2026"
    $t1 = 18 + [math]::Round([math]::Sin($d / 3.0) * 3, 1)
    $t2 = 20 + [math]::Round([math]::Cos($d / 4.0) * 2.5, 1)
    $ws.Cells.Item($r, 2).Formula = [string]$t1
    $ws.Cells.Item($r, 3).Formula = [string]$t2
    $ws.Cells.Item($r, 4).Formula = [string](45 + ($d % 7) * 2)
    $ws.Cells.Item($r, 5).Formula = "=IF(AND(B$r<=25,C$r<=25,D$r<=60),""в норме"",""отклонение"")"
    $ws.Cells.Item($r, 6).Value2 = $names[$d % 3]
    $ws.Cells.Item($r, 8).Value2 = $(if ($d -eq 12) { 'Проверка кондиционера' } else { '' })
  }
  $lo = $ws.ListObjects.Add(1, $ws.Range('A1:H31'), [Type]::Missing, 1)
  $lo.Name = 'Журнал'
  $ws.Range('A:H').EntireColumn.AutoFit() | Out-Null
  $chart = $ws.Shapes.AddChart2(-1, 4, 20, 520, 520, 260).Chart        # график
  $chart.SetSourceData($ws.Range('A1:C31'))
  $chart.HasTitle = $true; $chart.ChartTitle.Text = 'Температура на складах, ноябрь'
  $ws2 = $wb.Worksheets.Add([Type]::Missing, $ws)
  $ws2.Name = 'Сводка'
  # числа — через Formula: Value2 из PowerShell здесь принимает только текст
for ($r = 0; $r -lt $data.Count; $r++) { for ($c = 0; $c -lt 2; $c++) { $ws2.Cells.Item($r + 1, $c + 1).Formula = [string]$data[$r][$c] } }
  $ws2.Cells.Item(6, 1).Value2 = 'Итого'
  $ws2.Cells.Item(6, 2).Formula = '=SUM(B2:B5)'
  $ws2.Range('A1:B1').Font.Bold = $true
  $ws2.Range('A:B').EntireColumn.AutoFit() | Out-Null
  $pie = $ws2.Shapes.AddChart2(-1, 5, 160, 10, 360, 240).Chart         # круговая
  $pie.SetSourceData($ws2.Range('A1:B5'))
  $pie.HasTitle = $true; $pie.ChartTitle.Text = 'Доля отклонений по участкам'
  $ws.Activate()
  $wb.SaveAs((Join-Path $OutDir 'Журнал температур и отклонений.xlsx'), 51)
  $wb.Close($false)
} finally { $xl.Quit() }
'xlsx ok'
}

# ---------- PowerPoint ----------
if ($Parts -contains 'pptx') {
$pp = New-Object -ComObject PowerPoint.Application
try {
  $pres = $pp.Presentations.Add(0)
  $s1 = $pres.Slides.Add(1, 1)                 # титульный
  $s1.Shapes.Item(1).TextFrame.TextRange.Text = 'Гигиена рук на производстве'
  $s1.Shapes.Item(2).TextFrame.TextRange.Text = 'Обучение смены · СОП-ГП-004'
  $s2 = $pres.Slides.Add(2, 2)                 # заголовок и текст
  $s2.Shapes.Item(1).TextFrame.TextRange.Text = 'Когда обрабатывать руки'
  $s2.Shapes.Item(2).TextFrame.TextRange.Text = "Перед входом в чистую зону`rПосле перерыва и приёма пищи`rПосле контакта с отходами`rПеред сменой перчаток"
  $s3 = $pres.Slides.Add(3, 11)                # только заголовок
  $s3.Shapes.Item(1).TextFrame.TextRange.Text = 'Нарушения по участкам'
  $tbl = $s3.Shapes.AddTable($data.Count, 3, 40, 120, 640, 220).Table
  for ($r = 0; $r -lt $data.Count; $r++) { for ($c = 0; $c -lt 3; $c++) { $tbl.Cell($r + 1, $c + 1).Shape.TextFrame.TextRange.Text = [string]$data[$r][$c] } }
  $s4 = $pres.Slides.Add(4, 11)
  $s4.Shapes.Item(1).TextFrame.TextRange.Text = 'Динамика устранения'
  $ch = $s4.Shapes.AddChart2(-1, 51, 40, 110, 640, 380).Chart
  # данные — образцовые данные PowerPoint
  $ch.HasTitle = $true; $ch.ChartTitle.Text = 'Отклонения и устранение'
  $s5 = $pres.Slides.Add(5, 2)
  $s5.Shapes.Item(1).TextFrame.TextRange.Text = 'Скрытый слайд для докладчика'
  $s5.Shapes.Item(2).TextFrame.TextRange.Text = 'Этот слайд скрыт и не показывается сотрудникам'
  $s5.SlideShowTransition.Hidden = -1
  $pres.SaveAs((Join-Path $OutDir 'Гигиена рук — обучение.pptx'), 24)
  $pres.Close()
} finally { if ($pp.Presentations.Count -eq 0) { $pp.Quit() } }
'pptx ok'
}
Get-ChildItem $OutDir | Select-Object Name, Length
