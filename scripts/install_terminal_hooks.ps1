param(
    [switch]$FailClosed
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$guardPy = Join-Path $repoRoot "scripts\terminal_guard.py"
$cmdGuard = Join-Path $repoRoot "scripts\cmd_guard.ps1"

if (-not (Test-Path $guardPy)) {
    throw "Missing guard script: $guardPy"
}

function Ensure-ProfileHook {
    if (-not (Test-Path $PROFILE)) {
        New-Item -Type File -Path $PROFILE -Force | Out-Null
    }
    $content = Get-Content $PROFILE -Raw
    if ($content -match "SENTINEL_TERMINAL_GUARD_START") {
        Write-Host "PowerShell hook already present."
        return
    }

    $failClosedArg = if ($FailClosed) { " --fail-closed" } else { "" }

    $block = @"
# SENTINEL_TERMINAL_GUARD_START
`$global:SentinelGuardRepo = "$repoRoot"
`$global:SentinelGuardScript = Join-Path `$global:SentinelGuardRepo "scripts\terminal_guard.py"
`$global:SentinelGuardActive = `$false

function global:sentinel-bypass {
    param([Parameter(ValueFromRemainingArguments = `$true)] [string[]] `$Args)
    if (`$Args.Count -eq 0) { return }
    `$cmd = `$Args -join " "
    Invoke-Expression `$cmd
}

if (Get-Command Set-PSReadLineKeyHandler -ErrorAction SilentlyContinue) {
    Set-PSReadLineKeyHandler -Key Enter -ScriptBlock {
        param(`$key, `$arg)
        [string]`$line = ""
        [int]`$cursor = 0
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]`$line, [ref]`$cursor)

        if ([string]::IsNullOrWhiteSpace(`$line)) {
            [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
            return
        }

        if (`$line.TrimStart().StartsWith("sentinel-bypass ")) {
            [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
            return
        }

        if (`$global:SentinelGuardActive) {
            [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
            return
        }

        `$py = Get-Command python -ErrorAction SilentlyContinue
        if (-not `$py -or -not (Test-Path `$global:SentinelGuardScript)) {
            [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
            return
        }

        `$global:SentinelGuardActive = `$true
        try {
            python "`$global:SentinelGuardScript" --shell powershell --cwd "`$(Get-Location)" --command "`$line"$failClosedArg
            if (`$LASTEXITCODE -eq 0) {
                [Microsoft.PowerShell.PSConsoleReadLine]::AcceptLine()
            } else {
                Write-Host "[Sentinel] Command blocked: `$line" -ForegroundColor Red
                [Microsoft.PowerShell.PSConsoleReadLine]::Ding()
            }
        } finally {
            `$global:SentinelGuardActive = `$false
        }
    }
}
# SENTINEL_TERMINAL_GUARD_END
"@

    Add-Content -Path $PROFILE -Value "`r`n$block`r`n"
    Write-Host "PowerShell hook installed at $PROFILE"
}

function Ensure-CmdHook {
    $autoRun = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$cmdGuard`""
    $cmdKey = "HKCU:\Software\Microsoft\Command Processor"
    if (-not (Test-Path $cmdKey)) {
        New-Item -Path $cmdKey -Force | Out-Null
    }
    Set-ItemProperty -Path $cmdKey -Name "AutoRun" -Value $autoRun
    Write-Host "CMD hook installed via HKCU AutoRun."
}

function Ensure-BashHook {
    $bashrcCandidates = @(
        "$HOME\.bashrc",
        "$HOME\.zshrc"
    )

    $hook = @"
# SENTINEL_TERMINAL_GUARD_START
export SENTINEL_GUARD_SCRIPT="$guardPy"
sentinel-bypass() { command `"${@}`"; }
__sentinel_guard_preexec() {
  [ -n "`$SENTINEL_GUARD_ACTIVE" ] && return 0
  [ -z "`$BASH_COMMAND" ] && return 0
  case "`$BASH_COMMAND" in
    sentinel-bypass*|__sentinel_guard_preexec* ) return 0 ;;
  esac
  if command -v python >/dev/null 2>&1; then
    SENTINEL_GUARD_ACTIVE=1 python "`$SENTINEL_GUARD_SCRIPT" --shell bash --cwd "`$PWD" --command "`$BASH_COMMAND" >/dev/null 2>&1
    local rc=$?
    unset SENTINEL_GUARD_ACTIVE
    if [ `$rc -ne 0 ]; then
      echo "[Sentinel] blocked: `$BASH_COMMAND" >&2
      return 1
    fi
  fi
  return 0
}
if [ -n "`$BASH_VERSION" ]; then
  shopt -s extdebug
  trap '__sentinel_guard_preexec' DEBUG
fi
# SENTINEL_TERMINAL_GUARD_END
"@

    foreach ($path in $bashrcCandidates) {
        if (-not (Test-Path $path)) {
            New-Item -Type File -Path $path -Force | Out-Null
        }
        $content = Get-Content $path -Raw
        if ($content -match "SENTINEL_TERMINAL_GUARD_START") {
            Write-Host "Hook already present in $path"
            continue
        }
        Add-Content -Path $path -Value "`n$hook`n"
        Write-Host "Bash/Zsh hook added to $path"
    }
}

Ensure-ProfileHook
Ensure-CmdHook
Ensure-BashHook

Write-Host ""
Write-Host "Sentinel terminal hooks installed."
Write-Host "Open a new terminal window to start enforcement."
Write-Host "Use 'sentinel-bypass <command>' for emergency bypass."

