param([switch]$NoBrowser, [switch]$Preview)
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$baseUrl = 'http://127.0.0.1:8090'
$engineUrl = 'http://127.0.0.1:8081/api/v1/jobs'
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$kitRoot = Join-Path $projectRoot 'integrations\google-maps-scraper-kit'
$kitEnv = Join-Path $kitRoot '.env'
$composeFiles = @(
    'compose', '--env-file', $kitEnv,
    '-f', (Join-Path $kitRoot 'docker-compose.yml'),
    '-f', (Join-Path $projectRoot 'infrastructure\engine\compose.yaml')
)

function Get-LocalRuntime {
    try { return Invoke-RestMethod -Uri "$baseUrl/api/v1/runtime" -TimeoutSec 3 } catch { return $null }
}

function Test-DockerEngine {
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker info 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $previousPreference
    }
}

function Start-DockerDesktop {
    if (Test-DockerEngine) { return $false }
    if ($env:OS -ne 'Windows_NT') {
        throw 'Docker Engine is unavailable. Start Docker before using automated mode.'
    }

    $runningPath = Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue |
        Select-Object -First 1 -ExpandProperty Path
    $dockerCli = (Get-Command docker -ErrorAction SilentlyContinue).Source
    $dockerRoot = if ($dockerCli) {
        Split-Path (Split-Path (Split-Path $dockerCli -Parent) -Parent) -Parent
    }
    $candidates = @(@(
        $runningPath,
        (Join-Path $dockerRoot 'Docker Desktop.exe'),
        (Join-Path $dockerRoot 'frontend\Docker Desktop.exe'),
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Docker\Docker Desktop.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -Unique)

    if ($candidates.Count -eq 0) {
        throw 'Docker Desktop was not found in a supported location. Start it manually and run this command again.'
    }

    if (-not $runningPath) {
        Start-Process -FilePath $candidates[0] -WindowStyle Hidden
    }
    Write-Output 'Aguardando o Docker Engine...'
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        Start-Sleep -Seconds 2
        if (Test-DockerEngine) { return $true }
    }
    throw 'Docker Desktop opened, but the Docker Engine did not become available within 120 seconds.'
}

function Wait-ForUrl([string]$Url, [int]$Attempts = 30) {
    for ($attempt = 0; $attempt -lt $Attempts; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    return $false
}

$runtime = Get-LocalRuntime
if ($null -ne $runtime) {
    Write-Output "A aplicacao ja esta ativa em $baseUrl"
    Write-Output 'Use o terminal que iniciou essa instancia para encerra-la.'
    if (-not $NoBrowser) { Start-Process $baseUrl }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Ambiente Python ausente. Execute uv sync na raiz do projeto.'
}

$browserJob = $null

try {
    if (-not $Preview) {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw 'Docker CLI nao encontrado. Instale o Docker Desktop antes de usar o modo automatizado.'
        }
        if (-not (Test-Path -LiteralPath $kitEnv)) {
            throw 'Configuracao Docker ausente. Copie integrations/google-maps-scraper-kit/.env.example para .env e defina SCRAPER_PORT=8081.'
        }

        Start-DockerDesktop | Out-Null
        & docker @composeFiles up -d
        if ($LASTEXITCODE -ne 0) { throw 'Docker Compose could not start the Maps engine.' }

        if (-not (Wait-ForUrl $engineUrl 60)) {
            throw 'Maps engine did not become available at http://127.0.0.1:8081.'
        }
        Write-Output 'Motor Maps ativo em http://127.0.0.1:8081'
    }

    $pythonArguments = @(
        '-m', 'google_maps_scraper_kit',
        '--db', (Join-Path $projectRoot 'data\google-maps-scraper-kit.db'),
        'serve-api', '--host', '127.0.0.1', '--port', '8090'
    )
    if ($Preview) { $pythonArguments += '--no-worker' }

    if (-not $NoBrowser) {
        $browserJob = Start-Job -ScriptBlock {
            param($Url)
            for ($attempt = 0; $attempt -lt 30; $attempt++) {
                try {
                    Invoke-WebRequest -UseBasicParsing -Uri "$Url/api/v1/runtime" -TimeoutSec 3 | Out-Null
                    Start-Process $Url
                    return
                } catch {
                    Start-Sleep -Seconds 1
                }
            }
        } -ArgumentList $baseUrl
    }

    Write-Output "Iniciando aplicacao em $baseUrl"
    & $pythonPath @pythonArguments
    if ($LASTEXITCODE -ne 0) { throw "A aplicacao Python encerrou com codigo $LASTEXITCODE." }
} finally {
    if ($null -ne $browserJob) {
        Stop-Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job $browserJob -Force -ErrorAction SilentlyContinue
    }
}
