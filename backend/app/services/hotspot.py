"""«Мобильный хот-спот» Windows: постоянный адрес ноутбука 192.168.137.1 для телефонов.

Приложение на телефоне установлено с адреса хот-спота. После перезагрузки ноутбука Windows хот-спот
сама не включает — телефон не находит сеть, и ответы не уходят. Поэтому start.bat при запуске
проверяет хот-спот и, если он выключен, включает его штатным средством Windows (тот же переключатель,
что в «Параметры → Сеть и Интернет → Мобильный хот-спот»). Имя сети и пароль не меняются, телефон
подключается к ней сам. Отключить это поведение: переменная окружения LMS_HOTSPOT=off.
"""
from __future__ import annotations

import os
import subprocess

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
  Add-Type -AssemblyName System.Runtime.WindowsRuntime
  [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime] | Out-Null
  [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime] | Out-Null
  $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
  if ($null -eq $profile) { 'no-upstream'; exit }
  $m = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
  if ($m.TetheringOperationalState -eq 'On') { 'on'; exit }
  if (__CHECK_ONLY__) { 'off'; exit }
  $asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
  $op = $m.StartTetheringAsync()
  $task = $asTask.MakeGenericMethod([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]).Invoke($null, @($op))
  $task.Wait(20000) | Out-Null
  $res = $task.Result
  if ($res.Status -eq 'Success') { 'started' } else { 'failed: ' + $res.Status + ' ' + $res.AdditionalErrorMessage }
} catch { 'error: ' + $_.Exception.Message }
"""


def state(check_only: bool = False) -> str:
    """Состояние хот-спота: on, started, off, no-upstream, failed: …, error: …, skipped."""
    if os.name != "nt" or os.environ.get("LMS_HOTSPOT", "").lower() == "off":
        return "skipped"
    script = _SCRIPT.replace("__CHECK_ONLY__", "$true" if check_only else "$false")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                             capture_output=True, text=True, errors="replace", timeout=40,
                             creationflags=0x08000000)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "error: %s" % exc
    lines = [x.strip() for x in out.stdout.splitlines() if x.strip()]
    return lines[-1] if lines else "error: нет ответа"


def ensure_on() -> str:
    """Включает хот-спот, если он выключен. Возвращает строку для окна сервера."""
    s = state()
    if s == "on":
        return "Мобильный хот-спот включён — телефоны подключаются к постоянному адресу."
    if s == "started":
        return "Мобильный хот-спот был выключен — включён автоматически. Телефон подключится к сети ноутбука сам."
    if s == "skipped":
        return ""
    if s == "no-upstream":
        return ("Хот-спот не включён: ноутбук не подключён ни к одной сети, а Windows включает хот-спот только "
                "при подключении. Подключите Wi-Fi или кабель и запустите start.bat ещё раз.")
    return ("Не удалось проверить или включить мобильный хот-спот (%s). Включите вручную: Параметры → Сеть и "
            "Интернет → Мобильный хот-спот." % s)
