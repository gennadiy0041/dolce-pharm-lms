"""Сверка выгрузки «Время и входы» с экраном — и открытие файлов в настоящем Microsoft Excel.

Скачивает от имени методиста (и руководителя участка) экранные данные /api/reports/sessions и обе
выгрузки (XLSX и CSV) с одними и теми же фильтрами, затем открывает файлы в Excel через COM
и читает из него: названия листов, кириллицу в ячейках, итоговое время каждого сотрудника.

    python tools/check_time_export.py [--date-from 2026-11-20 --date-to 2026-11-21]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("LMS_ENV", "demo")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = ROOT / "e2e" / "results" / "time-export"

EXCEL_READ = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false; $xl.DisplayAlerts = $false
try {
  $res = @{}
  foreach ($path in $args) {
    $wb = $xl.Workbooks.Open($path, 0, $true)
    $sheets = @()
    foreach ($ws in $wb.Worksheets) {
      $used = $ws.UsedRange
      $rows = $used.Rows.Count; $cols = $used.Columns.Count
      $head = @(); for ($c = 1; $c -le $cols; $c++) { $head += [string]$ws.Cells.Item(1, $c).Text }
      $data = @()
      for ($r = 2; $r -le [Math]::Min($rows, 40); $r++) {
        $row = @(); for ($c = 1; $c -le $cols; $c++) { $row += [string]$ws.Cells.Item($r, $c).Text }
        $data += ,$row
      }
      $sheets += @{ name = [string]$ws.Name; rows = $rows; cols = $cols; head = $head; data = $data }
    }
    $wb.Close($false)
    $res[$path] = $sheets
  }
  $res | ConvertTo-Json -Depth 6 -Compress
} finally { $xl.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl) }
"""


def hms(seconds: int) -> str:
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date-from", default="")
    ap.add_argument("--date-to", default="")
    args = ap.parse_args()

    from app import config, seed

    base = "http://localhost:%d" % config.HTTP_PORT

    def login(name: str, password: str) -> str:
        req = urllib.request.Request(base + "/api/auth/login", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"login": name, "password": password, "device_id": "export-check",
                                                      "device_kind": "pc"}).encode())
        return json.loads(urllib.request.urlopen(req, timeout=30).read())["token"]

    def get(path: str, token: str) -> bytes:
        req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + token})
        return urllib.request.urlopen(req, timeout=60).read()

    name, password = next((a[0], a[1]) for a in seed.DEMO_ACCOUNTS if a[0] == "metodist")
    token = login(name, password)
    q = urllib.parse.urlencode({k: v for k, v in {"date_from": args.date_from, "date_to": args.date_to}.items() if v})
    q = ("?" + q) if q else ""
    screen = json.loads(get("/api/reports/sessions" + q, token))
    OUT.mkdir(parents=True, exist_ok=True)
    xlsx = OUT / "Время и входы.xlsx"
    xlsx.write_bytes(get("/api/reports/sessions.xlsx" + q, token))
    sep = "&" if q else "?"
    csv_totals = OUT / "Время и входы — итого.csv"
    csv_totals.write_bytes(get("/api/reports/sessions.csv" + q + sep + "table=totals", token))
    csv_sessions = OUT / "Время и входы — входы и выходы.csv"
    csv_sessions.write_bytes(get("/api/reports/sessions.csv" + q + sep + "table=sessions", token))
    # выход: проверка не должна оставлять в отчёте «идущих» сеансов методиста
    urllib.request.urlopen(urllib.request.Request(base + "/api/auth/logout", method="POST", data=b"",
                                                  headers={"Authorization": "Bearer " + token}), timeout=30).read()

    print("Экран: сотрудников %d, сеансов %d, строк по материалам %d"
          % (len(screen["totals"]), screen["sessions_total"], len(screen["materials"])))
    # -File, а не -Command: так пути с пробелами и кириллицей доходят до скрипта целиком
    script = OUT / "_excel_read.ps1"
    script.write_text(EXCEL_READ, encoding="utf-8-sig")
    ps = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                         str(xlsx), str(csv_totals), str(csv_sessions)],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    if ps.returncode != 0 or not ps.stdout.strip():
        print("Excel не открыл файлы:", ps.stderr[-800:])
        sys.exit(1)
    excel = json.loads(ps.stdout.strip().splitlines()[-1])
    report = {"filters": {"date_from": args.date_from, "date_to": args.date_to}, "screen": {
        "employees": len(screen["totals"]), "sessions": screen["sessions_total"], "materials": len(screen["materials"])}}

    sheets = excel[str(xlsx)]
    names = [s["name"] for s in sheets]
    totals_sheet = next(s for s in sheets if s["name"] == "Итого по сотрудникам")
    head = totals_sheet["head"]
    i_name, i_time, i_mat = head.index("ФИО"), head.index("Время на платформе"), head.index("Из него — изучение материалов")
    excel_rows = {r[i_name]: (r[i_time], r[i_mat]) for r in totals_sheet["data"] if r[i_name] and r[i_name] != "Всего"}
    mismatches = []
    for t in screen["totals"]:
        want = (hms(t["platform_seconds"]), hms(t["material_seconds"]))
        got = excel_rows.get(t["full_name"])
        if got != want:
            mismatches.append({"name": t["full_name"], "screen": want, "excel": got})
    sessions_sheet = next(s for s in sheets if s["name"] == "Входы и выходы")
    report["xlsx"] = {
        "sheets": names, "employees_rows": len(excel_rows), "sessions_rows": sessions_sheet["rows"] - 1,
        "totals_match_screen": not mismatches, "mismatches": mismatches,
        "cyrillic_sample": next(iter(excel_rows), ""), "header_sample": head[:5],
    }
    csv_t = excel[str(csv_totals)][0]
    i_name_c = csv_t["head"].index("ФИО")
    i_sec_c = csv_t["head"].index("Время на платформе, секунд")
    csv_secs = {r[i_name_c]: int(r[i_sec_c]) for r in csv_t["data"] if r[i_name_c]}
    report["csv"] = {
        "columns_in_excel": csv_t["cols"], "rows": csv_t["rows"] - 1,
        "totals_match_screen": all(csv_secs.get(t["full_name"]) == t["platform_seconds"] for t in screen["totals"]),
        "cyrillic_sample": next(iter(csv_secs), ""), "sessions_rows": excel[str(csv_sessions)][0]["rows"] - 1,
    }
    (OUT / "excel-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
