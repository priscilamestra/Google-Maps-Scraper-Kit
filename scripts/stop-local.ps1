$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$kitRoot = Join-Path $projectRoot 'integrations\google-maps-scraper-kit'
$kitEnv = Join-Path $kitRoot '.env'
$composeFiles = @(
    'compose', '--env-file', $kitEnv,
    '-f', (Join-Path $kitRoot 'docker-compose.yml'),
    '-f', (Join-Path $projectRoot 'infrastructure\engine\compose.yaml')
)

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

$runtime = try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8090/api/v1/runtime' -TimeoutSec 5
} catch {
    $null
}
if ($null -ne $runtime) {
    throw 'A aplicacao Python ainda esta ativa. Encerre-a com Ctrl+C no terminal que a iniciou.'
}

if (-not (Test-Path -LiteralPath $kitEnv)) {
    throw 'Configuracao Docker ausente em integrations/google-maps-scraper-kit/.env.'
}

if (-not (Test-DockerEngine)) {
    Write-Output 'Docker Engine ja esta desligado; nada para encerrar.'
    exit 0
}

& docker @composeFiles stop
if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel encerrar os containers do projeto.' }
Write-Output 'Containers do Google Maps Scraper Kit encerrados. Volumes e dados foram preservados.'
