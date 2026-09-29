$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo

git fetch origin
if ($LASTEXITCODE -ne 0) {
    throw "git fetch origin failed"
}
git checkout origin/main -- share-inbox
if ($LASTEXITCODE -ne 0) {
    throw "git checkout share-inbox failed"
}

$python = (Get-Command python -ErrorAction Stop).Source
& $python sfera_monitor.py --sync-share
exit $LASTEXITCODE
