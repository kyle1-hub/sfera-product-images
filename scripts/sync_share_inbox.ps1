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
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

git add -u -- share-inbox
git add -- share-inbox/.gitkeep
if (-not (git diff --cached --quiet -- share-inbox)) {
    git -c user.name="share-inbox-sync" -c user.email="share-inbox-sync@local" commit -m "Clear copied share-inbox zips [skip ci]"
    git pull --rebase origin main
    git push origin HEAD:main
}
