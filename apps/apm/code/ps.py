"""PowerShell hook execution and installer launching."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import messages as msg, paths, tools
from .errors import InstallError, ToolError
from .output import console
from .util import split_windows_args


def find_powershell() -> str:
    for name in ("powershell.exe", "pwsh.exe"):
        found = tools.find_executable(name)
        if found:
            return str(found)
    raise ToolError(msg.ERR_POWERSHELL_MISSING)


def _quote(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


# 注入到所有内联 PowerShell 脚本的同名 helper（Add-Path / Remove-Path）。
# 语义对齐 design.md 11.3：只写 HKCU\Environment\Path，保留原值类型，
# 去重规则忽略大小写、首尾引号与末尾反斜杠，新增前置，写回后广播 WM_SETTINGCHANGE。
HELPER_FUNCTIONS = r"""
function ApmNormalizePath([string]$Value) {
    return ($Value.Trim().Trim('"').TrimEnd('\')).ToLowerInvariant()
}

function Apm-BroadcastEnvironment {
    if (-not ('Apm.EnvBroadcast' -as [type])) {
        Add-Type -Namespace Apm -Name EnvBroadcast -MemberDefinition '[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);' | Out-Null
    }
    $result = [UIntPtr]::Zero
    [Apm.EnvBroadcast]::SendMessageTimeout([IntPtr]0xFFFF, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result) | Out-Null
}

function appdir($app, $global = $false) {
    if ($global) { throw 'APM 仅支持用户级安装，appdir 不支持 -Global' }
    if ($app -eq 'scoop' -or $app -eq 'apm') { return $scoopdir }
    return Join-Path $scoopdir "apps\$app"
}

function versiondir($app, $version, $global = $false) {
    return Join-Path (appdir $app $global) $version
}

function currentdir($app, $global = $false) {
    return Join-Path (appdir $app $global) 'current'
}

function persistdir($app, $global = $false) {
    if ($global) { throw 'APM 仅支持用户级安装，persistdir 不支持 -Global' }
    return Join-Path $scoopdir "persist\$app"
}

function Add-Path {
    param(
        [string[]]$Path,
        [string]$TargetEnvVar = 'PATH',
        [switch]$Global,
        [switch]$Force,
        [switch]$Quiet
    )
    if ($Global) { throw 'APM 仅支持用户级 PATH，Add-Path 不支持 -Global' }
    if ($TargetEnvVar -ne 'PATH') { throw "APM 仅支持写入 PATH，不支持目标变量：$TargetEnvVar" }
    $entries = @($Path | Where-Object { $_ -and $_.Trim() -ne '' })
    if (!$entries) { return }
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    if (!$key) { throw '无法打开注册表 HKCU\Environment' }
    try {
        $options = [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        $raw = [string]$key.GetValue('Path', '', $options)
        try {
            $kind = $key.GetValueKind('Path')
        } catch {
            if ($raw -like '*%*') { $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
            else { $kind = [Microsoft.Win32.RegistryValueKind]::String }
        }
        $parts = @($raw -split ';' | Where-Object { $_.Trim() -ne '' })
        if ($Force) {
            $drop = @{}
            foreach ($p in $entries) { $drop[(ApmNormalizePath $p)] = $true }
            $parts = @($parts | Where-Object { !$drop.ContainsKey((ApmNormalizePath $_)) })
            $prepend = @()
            foreach ($p in $entries) { $prepend = @($p) + $prepend }
        } else {
            $seen = @{}
            foreach ($p in $parts) { $seen[(ApmNormalizePath $p)] = $true }
            $prepend = @()
            foreach ($p in $entries) {
                if (!$seen.ContainsKey((ApmNormalizePath $p))) {
                    $seen[(ApmNormalizePath $p)] = $true
                    $prepend = @($p) + $prepend
                }
            }
        }
        if (!$prepend) { return }
        $key.SetValue('Path', (@($prepend) + $parts) -join ';', $kind)
        if (!$Quiet) {
            foreach ($p in $prepend) { Write-Host "加入用户 PATH：$p" }
        }
    } finally {
        $key.Close()
    }
    $env:PATH = (@($entries) + @($env:PATH -split ';' | Where-Object { $_.Trim() -ne '' })) -join ';'
    Apm-BroadcastEnvironment
}

function Remove-Path {
    param(
        [string[]]$Path,
        [string]$TargetEnvVar = 'PATH',
        [switch]$Global,
        [switch]$Force,
        [switch]$Quiet
    )
    if ($Global) { throw 'APM 仅支持用户级 PATH，Remove-Path 不支持 -Global' }
    if ($TargetEnvVar -ne 'PATH') { throw "APM 仅支持写入 PATH，不支持目标变量：$TargetEnvVar" }
    $entries = @($Path | Where-Object { $_ -and $_.Trim() -ne '' })
    if (!$entries) { return }
    $drop = @{}
    foreach ($p in $entries) { $drop[(ApmNormalizePath $p)] = $true }
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment', $true)
    if (!$key) { throw '无法打开注册表 HKCU\Environment' }
    try {
        $options = [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames
        $raw = [string]$key.GetValue('Path', '', $options)
        try {
            $kind = $key.GetValueKind('Path')
        } catch {
            if ($raw -like '*%*') { $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString }
            else { $kind = [Microsoft.Win32.RegistryValueKind]::String }
        }
        $parts = @($raw -split ';' | Where-Object { $_.Trim() -ne '' })
        $remaining = @($parts | Where-Object { !$drop.ContainsKey((ApmNormalizePath $_)) })
        if ($remaining.Count -eq $parts.Count) { return }
        $key.SetValue('Path', ($remaining -join ';'), $kind)
        if (!$Quiet) {
            foreach ($p in $entries) { Write-Host "从用户 PATH 移除：$p" }
        }
    } finally {
        $key.Close()
    }
    $env:PATH = (@($env:PATH -split ';') | Where-Object { $_.Trim() -ne '' -and !$drop.ContainsKey((ApmNormalizePath $_)) }) -join ';'
    Apm-BroadcastEnvironment
}
"""


def run_script(script: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    powershell = find_powershell()
    temp_dir = Path(tempfile.mkdtemp(prefix="apm-ps-"))
    script_path = temp_dir / "script.ps1"
    try:
        script_path.write_text(
            HELPER_FUNCTIONS + "\r\n" + script,
            encoding="utf-8-sig",
            newline="\r\n",
        )
        return subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _prelude(context: dict[str, Any]) -> list[str]:
    """内联 PowerShell 的变量前置段；钩子与 installer 共用同一构造。

    context 键名固定为：app、version、bucket、directory、original_dir、
    persist_dir、architecture、fname、manifest_path、cmd。fname 支持
    字符串（installer 场景）与字符串列表（钩子场景）。
    """
    fname = context["fname"]
    if isinstance(fname, list):
        fname_expr = "@(" + ", ".join(_quote(item) for item in fname) + ")"
    else:
        fname_expr = _quote(fname)
    return [
        f"$app = {_quote(context['app'])}",
        f"$version = {_quote(context['version'])}",
        f"$bucket = {_quote(context['bucket'])}",
        f"$dir = {_quote(context['directory'])}",
        f"$original_dir = {_quote(context['original_dir'])}",
        f"$persist_dir = {_quote(context['persist_dir'])}",
        "$global = $false",
        f"$architecture = {_quote(context['architecture'])}",
        f"$fname = {fname_expr}",
        f"$cmd = {_quote(context['cmd'])}",
        f"$bucketsdir = {_quote(paths.BUCKETS_DIR)}",
        f"$cachedir = {_quote(paths.CACHE_DIR)}",
        f"$scoopdir = {_quote(paths.ROOT)}",
        f"$manifest = Get-Content -LiteralPath {_quote(context['manifest_path'])} -Raw -Encoding UTF8 | ConvertFrom-Json",
    ]


def run_hook(
    hook_type: str,
    script: str | list[str] | None,
    *,
    context: dict[str, Any],
) -> None:
    """执行 manifest 钩子脚本，上下文由统一的 context 字典提供。"""
    if not script:
        return
    body = script if isinstance(script, str) else "\n".join(str(item) for item in script)
    if not body.strip():
        return
    hook_name = msg.HOOK_NAMES.get(hook_type, hook_type)
    console.detail(msg.MSG_HOOK_RUNNING.format(hook=hook_name))
    prelude = _prelude(context)
    prelude.append(body)
    proc = run_script("\r\n".join(prelude), cwd=context["original_dir"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise InstallError(
            msg.ERR_HOOK_FAILED.format(
                hook=hook_name, code=proc.returncode, detail=detail
            )
        )


def run_installer(
    executable: Path,
    args: str,
    cwd: Path,
    *,
    context: dict[str, Any],
) -> None:
    """运行安装器/卸载器，context 与 run_hook 共用同一键名约定。"""
    if not executable.is_file():
        raise InstallError(msg.ERR_INSTALLER_MISSING.format(path=executable))
    suffix = executable.suffix.lower()
    if suffix == ".ps1":
        prelude = _prelude(context)
        prelude.append(f"& {_quote(executable)} {args}")
        proc = run_script("\r\n".join(prelude), cwd=cwd)
    elif suffix == ".msi":
        cmd = ["msiexec", "/i", str(executable)]
        cmd.extend(split_windows_args(args))
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    elif suffix in (".cmd", ".bat"):
        proc = subprocess.run(
            ["cmd", "/c", str(executable), *split_windows_args(args)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    else:
        proc = subprocess.run(
            [str(executable), *split_windows_args(args)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise InstallError(
            msg.ERR_INSTALLER_FAILED.format(
                code=proc.returncode,
                name=executable.name,
                detail=detail,
            )
        )
