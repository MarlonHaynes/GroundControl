<#
.SYNOPSIS
  Windows shim for the Makefile. Same targets, same commands.

.DESCRIPTION
  GNU Make is not installed by default on Windows. The Makefile remains the
  canonical definition of these tasks (and is what CI runs); this script
  dispatches the identical underlying commands so a Windows developer needs
  no extra tooling.

.EXAMPLE
  .\make.ps1 up
  .\make.ps1 test
  .\make.ps1 revision -m "add widgets"
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$Compose = @('docker', 'compose')
$ApiExec = @('docker', 'compose', 'exec', '-T', 'api')

function Invoke-Native {
    param([string[]]$Cmd)
    Write-Host "> $($Cmd -join ' ')" -ForegroundColor DarkGray
    & $Cmd[0] @($Cmd[1..($Cmd.Length - 1)])
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED (exit $LASTEXITCODE): $($Cmd -join ' ')" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Get-MessageArg {
    # Supports both `.\make.ps1 revision -m "msg"` and `.\make.ps1 revision "msg"`
    if (-not $Rest) { return '' }
    for ($i = 0; $i -lt $Rest.Count; $i++) {
        if ($Rest[$i] -eq '-m' -and ($i + 1) -lt $Rest.Count) { return $Rest[$i + 1] }
    }
    return $Rest[0]
}

switch ($Target) {
    'help' {
        Write-Host ""
        Write-Host "GroundControl targets" -ForegroundColor Cyan
        Write-Host ""
        $targets = [ordered]@{
            'up'        = 'Start postgres + api + web'
            'dev'       = 'Bring the stack up and follow logs'
            'down'      = 'Stop the stack (keeps the database volume)'
            'logs'      = 'Follow logs for all services'
            'build'     = 'Rebuild images'
            'seed'      = 'Load fixtures into a coherent demo state'
            'eval'      = 'Run the eval harness on a 20-case subset'
            'eval-full' = 'Run the eval harness on the full dataset'
            'test'      = 'Run the test suite (no API calls, free)'
            'test-cov'  = 'Run tests with a coverage report'
            'lint'      = 'Lint the API and the web app'
            'fmt'       = 'Format the API and the web app'
            'types'     = 'Regenerate TS types from the OpenAPI spec'
            'migrate'   = 'Apply migrations'
            'revision'  = 'Autogenerate a migration: .\make.ps1 revision -m "msg"'
            'psql'      = 'Open a psql shell'
            'shell'     = 'Open a shell in the api container'
            'clean'     = 'Remove build artifacts and caches'
            'nuke'      = 'Stop everything and DESTROY the database volume'
        }
        foreach ($k in $targets.Keys) {
            Write-Host ("  {0,-12} {1}" -f $k, $targets[$k])
        }
        Write-Host ""
    }
    'up' {
        Invoke-Native ($Compose + @('up', '-d', '--build'))
        Write-Host "api  -> http://localhost:8000/docs" -ForegroundColor Green
        Write-Host "web  -> http://localhost:3000" -ForegroundColor Green
    }
    'dev' {
        Invoke-Native ($Compose + @('up', '-d', '--build'))
        Invoke-Native ($Compose + @('logs', '-f'))
    }
    'down'      { Invoke-Native ($Compose + @('down')) }
    'logs'      { Invoke-Native ($Compose + @('logs', '-f')) }
    'build'     { Invoke-Native ($Compose + @('build')) }
    'seed'      { Invoke-Native ($ApiExec + @('python', '-m', 'scripts.seed')) }
    'eval'      { Invoke-Native ($ApiExec + @('python', '-m', 'evals.run', '--limit', '20')) }
    'eval-full' { Invoke-Native ($ApiExec + @('python', '-m', 'evals.run')) }
    'test'      { Invoke-Native ($ApiExec + @('python', '-m', 'pytest', '-q', '-m', 'not llm')) }
    'test-cov'  { Invoke-Native ($ApiExec + @('python', '-m', 'pytest', '-q', '-m', 'not llm', '--cov=.', '--cov-report=term-missing')) }
    'lint' {
        Invoke-Native ($ApiExec + @('ruff', 'check', '.'))
        Push-Location apps/web; try { Invoke-Native @('npm', 'run', 'lint') } finally { Pop-Location }
    }
    'fmt' {
        Invoke-Native ($ApiExec + @('ruff', 'format', '.'))
        Invoke-Native ($ApiExec + @('ruff', 'check', '--fix', '.'))
        Push-Location apps/web; try { Invoke-Native @('npm', 'run', 'fmt') } finally { Pop-Location }
    }
    'types' {
        Push-Location apps/web; try { Invoke-Native @('npm', 'run', 'gen:types') } finally { Pop-Location }
    }
    'migrate'  { Invoke-Native ($ApiExec + @('alembic', 'upgrade', 'head')) }
    'revision' {
        $m = Get-MessageArg
        if (-not $m) { Write-Host 'Usage: .\make.ps1 revision -m "message"' -ForegroundColor Red; exit 1 }
        Invoke-Native ($ApiExec + @('alembic', 'revision', '--autogenerate', '-m', $m))
    }
    'psql'  { Invoke-Native ($Compose + @('exec', 'db', 'psql', '-U', 'groundcontrol', '-d', 'groundcontrol')) }
    'shell' { Invoke-Native ($Compose + @('exec', 'api', 'bash')) }
    'clean' {
        Invoke-Native ($Compose + @('down', '--remove-orphans'))
        foreach ($p in @('apps/web/.next', 'services/api/.pytest_cache', 'services/api/.ruff_cache')) {
            if (Test-Path $p) { Remove-Item -Recurse -Force $p }
        }
    }
    'nuke'  { Invoke-Native ($Compose + @('down', '-v', '--remove-orphans')) }
    default {
        Write-Host "Unknown target '$Target'. Run .\make.ps1 help" -ForegroundColor Red
        exit 1
    }
}
