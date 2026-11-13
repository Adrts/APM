$env:APM_ROOT = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $env:APM_ROOT '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Virtual environment not found. Run shims\apm_setup.cmd first.'
    exit 1
}
$env:PYTHONUTF8 = '1'
& $python (Join-Path $env:APM_ROOT 'apps\apm\code\__main__.py') @args
exit $LASTEXITCODE
