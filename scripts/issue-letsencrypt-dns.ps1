param(
    [Parameter(Mandatory = $true)]
    [string]$Email,
    [switch]$Staging
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose-prod.yml"
$httpsTemplate = Join-Path $projectRoot "nginx\templates\agent-https.conf"
$activeConfig = Join-Path $projectRoot "nginx\conf.d\agent.conf"
$certificateName = if ($Staging) { "android.isicom.pt-dns-staging" } else { "android.isicom.pt" }

Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path "certbot\www", "certbot\conf" | Out-Null

$certbotArgs = @(
    "compose", "-f", $composeFile, "--profile", "certbot", "run", "--rm",
    "certbot", "certonly", "--manual", "--preferred-challenges", "dns",
    "--domain", "android.isicom.pt", "--cert-name", $certificateName,
    "--email", $Email, "--agree-tos", "--no-eff-email"
)
if ($Staging) { $certbotArgs += "--staging" }

Write-Host "Certbot solicitará crear este registro DNS:"
Write-Host "  Tipo: TXT"
Write-Host "  Nombre: _acme-challenge.android.isicom.pt"
Write-Host "No pulses Enter hasta comprobar su propagación con:"
Write-Host "  Resolve-DnsName _acme-challenge.android.isicom.pt -Type TXT -Server 8.8.8.8"

& docker @certbotArgs
if ($LASTEXITCODE -ne 0) { throw "Certbot no pudo completar la validación DNS-01." }

if ($Staging) {
    Write-Host "Certificado DNS de staging emitido. No se activó HTTPS de producción."
    exit 0
}

Copy-Item -LiteralPath $httpsTemplate -Destination $activeConfig -Force
docker compose -f $composeFile up -d --force-recreate nginx
if ($LASTEXITCODE -ne 0) { throw "El certificado se emitió, pero Nginx no pudo activar HTTPS." }
docker exec machining_nginx nginx -t
if ($LASTEXITCODE -ne 0) { throw "La configuración HTTPS de Nginx no es válida." }

Write-Host "HTTPS activado: https://android.isicom.pt/"
Write-Warning "DNS-01 manual no se renueva automáticamente. Repite este script antes del vencimiento o configura la API DNS del proveedor."
