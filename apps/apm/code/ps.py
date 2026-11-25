"""PowerShell hook execution and installer launching."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import config, messages as msg, paths, tools
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
# Scoop 源里的 manifest 脚本会直接调用 Scoop 自带 helper；这里补齐 APM 支持的同名函数，
# 避免 $ErrorActionPreference = 'Stop' 下因 CommandNotFoundException 中断钩子。
HELPER_FUNCTIONS = r"""
# 消息 helper：与 Scoop lib/core.ps1 同名同行为（仅写宿主，不抛错）。
function abort($msg, [int]$exit_code = 1) { Write-Host $msg -ForegroundColor Red; exit $exit_code }
function error($msg) { Write-Host "ERROR $msg" -ForegroundColor DarkRed }
function warn($msg) { Write-Host "WARN  $msg" -ForegroundColor DarkYellow }
function info($msg) { Write-Host "INFO  $msg" -ForegroundColor DarkGray }

# 目录 helper：Scoop 的 ensure 返回 Convert-Path 结果，供调用方继续拼接路径。
function ensure($dir) {
    if (!(Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Convert-Path -LiteralPath $dir
}

# 外部命令 helper：对齐 Scoop 的 Invoke-ExternalCommand（含 Path/Args/Msg/cec/Log 别名）。
# 与 Scoop 一致返回 $true/$false，非零退出由调用方决定是否 abort。
function Invoke-ExternalCommand {
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [Alias('Path')]
        [string]$FilePath,
        [Parameter(Position = 1)]
        [Alias('Args')]
        [string[]]$ArgumentList = @(),
        [switch]$RunAs,
        [switch]$Quiet,
        [Alias('Msg')]
        [string]$Activity,
        [Alias('cec')]
        [hashtable]$ContinueExitCodes,
        [Alias('Log')]
        [string]$LogPath
    )
    if ($Activity) { Write-Host "$Activity " -NoNewline }
    $callArgs = @($ArgumentList | Where-Object { $null -ne $_ })
    $code = 0
    try {
        if ($RunAs -or $Quiet) {
            $startArgs = @{ FilePath = $FilePath; ArgumentList = $callArgs; Wait = $true; PassThru = $true }
            if ($Quiet) { $startArgs['WindowStyle'] = 'Hidden' }
            if ($RunAs) { $startArgs['Verb'] = 'RunAs' }
            $code = (Start-Process @startArgs).ExitCode
        } else {
            & $FilePath @callArgs
            $code = $LASTEXITCODE
        }
    } catch {
        if ($Activity) { Write-Host 'Error.' -ForegroundColor DarkRed }
        error "无法执行 $FilePath：$($_.Exception.Message)"
        return $false
    }
    if ($code -ne 0) {
        if ($ContinueExitCodes -and $ContinueExitCodes.ContainsKey($code)) {
            if ($Activity) { Write-Host 'Done.' -ForegroundColor DarkYellow }
            warn $ContinueExitCodes[$code]
            return $true
        }
        if ($Activity) { Write-Host 'Error.' -ForegroundColor DarkRed }
        error "Exit code was $code!"
        return $false
    }
    if ($Activity) { Write-Host 'Done.' -ForegroundColor Green }
    return $true
}

# ExtractDir 解压后把子目录内容搬到目标根，按 Scoop movedir 的语义合并。
function Apm-MoveDirContents([string]$From, [string]$To) {
    if (!(Test-Path -LiteralPath $From -PathType Container)) { return }
    ensure $To | Out-Null
    Get-ChildItem -LiteralPath $From -Force | ForEach-Object {
        $dest = Join-Path $To $_.Name
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        Move-Item -LiteralPath $_.FullName -Destination $dest -Force
    }
    Remove-Item -LiteralPath $From -Recurse -Force
}

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

function Expand-7zipArchive {
    param(
        [Parameter(Mandatory = $true, Position = 0)] [string]$Path,
        [Parameter(Position = 1)] [string]$DestinationPath = (Split-Path $Path),
        [string]$ExtractDir,
        [Parameter(ValueFromRemainingArguments = $true)] [string]$Switches,
        [ValidateSet('All', 'Skip', 'Rename')] [string]$Overwrite,
        [Alias('RemoveArchive')] [switch]$Removal
    )
    if (-not $internal_seven_zip) {
        throw '未检测到 7z.exe，无法执行 Expand-7zipArchive（可在 config.json 的 extract.seven_zip 显式配置）'
    }
    $DestinationPath = $DestinationPath.TrimEnd('\')
    $argList = @('x', $Path, "-o$DestinationPath", '-xr!*.nsis', '-y', '-bso0', '-bsp0')
    if ($ExtractDir) { $argList += "-ir!$ExtractDir\*" }
    if ($Switches) { $argList += (-split $Switches) }
    switch ($Overwrite) {
        'All' { $argList += '-aoa' }
        'Skip' { $argList += '-aos' }
        'Rename' { $argList += '-aou' }
    }
    & $internal_seven_zip @argList
    if ($LASTEXITCODE -ne 0) {
        throw "Expand-7zipArchive 解压失败（退出码 $LASTEXITCODE）：$Path"
    }
    if ($ExtractDir) {
        Apm-MoveDirContents "$DestinationPath\$ExtractDir" $DestinationPath
        $extractTop = "$DestinationPath\$($ExtractDir -replace '[\\/].*', '')"
        if ((Test-Path -LiteralPath $extractTop -PathType Container) -and !(Get-ChildItem -LiteralPath $extractTop -Force)) {
            Remove-Item -LiteralPath $extractTop -Recurse -Force
        }
    }
    if ($Removal) { Remove-Item -LiteralPath $Path -Force }
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
        # PowerShell 5.1 重定向输出默认使用系统 ANSI 代码页（中文系统为 GBK），
        # 而下方按 UTF-8 解码；先强制子进程以 UTF-8 写出，避免中文报错变乱码。
        # PowerShell 5.1 中原生命令重定向 stderr（如 Scoop 清单常见的 `*>$null`）会被
        # 包装成 NativeCommandError；即使退出码为 0，在 EAP=Stop 下也会终止脚本。
        # Scoop 自身不设置 EAP=Stop，因此这类写法在其上正常。这里只放行这一种错误，
        # 其余错误仍按 fail-loud 终止。
        # $ErrorActionPreference = 'Stop'：cmdlet 报错即终止（退出码非 0），
        # 避免 Scoop helper 缺失等异常被 Continue 吞掉后安装出隐形残缺应用。
        script_path.write_text(
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\r\n"
            "$ErrorActionPreference = 'Stop'\r\n"
            "trap {\r\n"
            "    if ($_.FullyQualifiedErrorId -eq 'NativeCommandError') { continue }\r\n"
            "    throw $_\r\n"
            "}\r\n"
            + HELPER_FUNCTIONS
            + "\r\n"
            + script,
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
    # Expand-7zipArchive 按同一查找顺序（显式配置 → shims → PATH）解析 7z，
    # 与解压模块共用同一工具；未找到时注入空串，函数被调用时显式报错。
    seven_zip = tools.find_7z(config.load())
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
        f"$internal_seven_zip = {_quote(str(seven_zip) if seven_zip else '')}",
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
