param(
    [Parameter(Mandatory = $true)]
    [string]$Command
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$guard = Join-Path $repoRoot "scripts\terminal_guard.py"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[Sentinel] python not found; allowing command."
    cmd /d /c $Command
    exit $LASTEXITCODE
}

python $guard --shell cmd --cwd "$PWD" --command "$Command"
if ($LASTEXITCODE -eq 0) {
    cmd /d /c $Command
    exit $LASTEXITCODE
}

exit 1

