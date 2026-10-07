[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstanceCode,

    [string[]]$Tables,

    [ValidateNotNullOrEmpty()]
    [string]$ApiBaseUrl = "http://localhost:8000",

    [ValidateRange(2, 300)]
    [int]$PollSeconds = 10,

    [ValidateRange(0, 10080)]
    [int]$MaxWaitMinutes = 0,

    [string]$AdminKey
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)\s*$')) {
            $value = $Matches[1].Trim()
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $null
}

if ([string]::IsNullOrWhiteSpace($AdminKey)) {
    $AdminKey = [Environment]::GetEnvironmentVariable("HISTORICAL_INGESTION_ADMIN_KEY")
}
if ([string]::IsNullOrWhiteSpace($AdminKey)) {
    $AdminKey = Get-DotEnvValue -Path (Join-Path $projectRoot ".env") `
        -Name "HISTORICAL_INGESTION_ADMIN_KEY"
}
if ([string]::IsNullOrWhiteSpace($AdminKey)) {
    throw "No se encontró HISTORICAL_INGESTION_ADMIN_KEY en el entorno ni en .env."
}

$baseUrl = $ApiBaseUrl.TrimEnd('/')
$headers = @{ "X-Agent-Admin-Key" = $AdminKey }
$payload = @{ instanceCode = $InstanceCode }
if ($Tables -and $Tables.Count -gt 0) {
    $payload.tables = @($Tables)
}

try {
    $startResponse = Invoke-RestMethod `
        -Method Post `
        -Uri "$baseUrl/api/v1/agent/system-knowledge-ingestion/start" `
        -Headers $headers `
        -ContentType "application/json" `
        -Body ($payload | ConvertTo-Json -Depth 4) `
        -TimeoutSec 30
}
catch {
    throw "No se pudo iniciar la ingesta en $baseUrl. Comprueba la API, la clave y la instancia. $($_.Exception.Message)"
}

$runId = [string]$startResponse.runId
if ([string]::IsNullOrWhiteSpace($runId)) {
    throw "La API no devolvió un runId."
}

$startedAt = Get-Date
Write-Host "Ingesta encolada. Run ID: $runId"
Write-Host "Instancia: $InstanceCode"
Write-Host ($(if ($Tables) { "Tablas: " + ($Tables -join ', ') } else { "Tablas: alcance completo predeterminado" }))
Write-Host "Pulsa Ctrl+C para dejar de observar; la ingesta continuará en el worker."

$terminalStates = @("completed", "failed", "cancelled")
while ($true) {
    Start-Sleep -Seconds $PollSeconds
    $status = Invoke-RestMethod `
        -Method Get `
        -Uri "$baseUrl/api/v1/agent/system-knowledge-ingestion/status?runId=$runId" `
        -Headers $headers `
        -TimeoutSec 30

    $elapsed = (Get-Date) - $startedAt
    $state = [string]$status.Status
    $tablesCompleted = [int]$status.TablesCompleted
    $tablesTotal = [int]$status.TablesTotal
    $rowsRead = [long]$status.RowsRead
    $rowsIndexed = [long]$status.RowsIndexed
    $rowsSkipped = [long]$status.RowsSkipped
    $currentTable = if ($status.CurrentTable) { [string]$status.CurrentTable } else { "-" }

    $etaText = "ETA no disponible"
    if ($tablesCompleted -gt 0 -and $tablesTotal -gt $tablesCompleted) {
        $remainingSeconds = ($elapsed.TotalSeconds / $tablesCompleted) * ($tablesTotal - $tablesCompleted)
        $etaText = "ETA aproximada por tablas: " + [TimeSpan]::FromSeconds($remainingSeconds).ToString("d\.hh\:mm\:ss")
    }

    Write-Host ("[{0}] estado={1} tabla={2} tablas={3}/{4} leídas={5} indexadas={6} sin cambios/omitidas={7} transcurrido={8} {9}" -f `
        (Get-Date -Format "HH:mm:ss"), $state, $currentTable, $tablesCompleted, $tablesTotal, `
        $rowsRead, $rowsIndexed, $rowsSkipped, $elapsed.ToString("d\.hh\:mm\:ss"), $etaText)

    if ($terminalStates -contains $state.ToLowerInvariant()) {
        if ($state -ne "completed") {
            throw "La ingesta terminó con estado '$state': $($status.Error)"
        }
        Write-Host "Ingesta completada correctamente en $($elapsed.ToString('d\.hh\:mm\:ss'))."
        break
    }
    if ($MaxWaitMinutes -gt 0 -and $elapsed.TotalMinutes -ge $MaxWaitMinutes) {
        Write-Warning "Se deja de observar tras $MaxWaitMinutes minutos. La ejecución $runId continúa en el worker."
        break
    }
}
