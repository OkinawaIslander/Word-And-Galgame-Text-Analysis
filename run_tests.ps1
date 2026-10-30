$ErrorActionPreference = "Stop"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python was not found. Install Python 3.10+ and add it to PATH."
    exit 1
}

$env:PYTHONPATH = Join-Path $PSScriptRoot "src"
& python -m unittest discover -v
exit $LASTEXITCODE
