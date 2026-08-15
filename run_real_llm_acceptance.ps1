$ErrorActionPreference = 'Stop'
$repoRoot = $PSScriptRoot
$runDir = Join-Path $repoRoot 'runs\demo_v1_acceptance'
New-Item -ItemType Directory -Force -Path $runDir | Out-Null

if ([string]::IsNullOrWhiteSpace($env:ONEAPI_API_KEY)) {
    throw 'ONEAPI_API_KEY is not configured for the Server process.'
}

$stdout = Join-Path $runDir 'server_stdout.log'
$stderr = Join-Path $runDir 'server_stderr.log'
$server = $null
try {
    $server = Start-Process -FilePath 'uv' `
        -ArgumentList @(
            'run', 'python', '-m', 'worldir_agent.server',
            '--config', 'config/config.oneapi.deepseek-v4-flash.toml'
        ) `
        -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr `
        -PassThru

    $healthy = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 1
            if ($response.status -eq 'ok') {
                $healthy = $true
                break
            }
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
    if (-not $healthy) {
        throw 'Real World Compiler Server did not become healthy.'
    }

    & uv run --project $repoRoot python (Join-Path $repoRoot 'scripts\run_real_demo_acceptance.py')
    if ($LASTEXITCODE -ne 0) {
        throw "Real LLM acceptance failed with exit code $LASTEXITCODE."
    }
} finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force
    }
}
