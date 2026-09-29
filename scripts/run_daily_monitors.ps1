$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Repo

$LogDir = Join-Path $Repo "logs"
if (-not (Test-Path -LiteralPath $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}
$LogFile = Join-Path $LogDir ("daily_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"===== $stamp START =====" | Out-File -FilePath $LogFile -Append -Encoding utf8

$python = (Get-Command python -ErrorAction Stop).Source
$sites = @("sfera", "bijou", "lovisa", "stradivarius", "primark")
$failed = @()
foreach ($site in $sites) {
    "----- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') site=$site -----" | Out-File -FilePath $LogFile -Append -Encoding utf8
    & $python (Join-Path $Repo "sfera_monitor.py") --site $site 2>&1 | Out-File -FilePath $LogFile -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) {
        $failed += $site
        "site=$site exit=$LASTEXITCODE" | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
}

$end = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if ($failed.Count) {
    "===== $end END failed=$($failed -join ',') =====" | Out-File -FilePath $LogFile -Append -Encoding utf8
    exit 1
}
"===== $end END ok =====" | Out-File -FilePath $LogFile -Append -Encoding utf8
exit 0
