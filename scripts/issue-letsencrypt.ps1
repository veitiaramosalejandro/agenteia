param(
    [Parameter(Mandatory = $true)]
    [string]$Email,
    [switch]$Staging
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose-prod.yml"
$httpTemplate = Join-Path $projectRoot "nginx\templates\agent-http.conf"
$httpsTemplate = Join-Path $projectRoot "nginx\templates\agent-https.conf"
$activeConfig = Join-Path $projectRoot "nginx\conf.d\agent.conf"

Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path "certbot\www", "certbot\conf" | Out-Null

# Nginx debe arrancar sin referencias a certificados antes de la primera emisión.
Copy-Item -LiteralPath $httpTemplate -Destination $activeConfig -Force
docker compose -f $composeFile up -d nginx
if ($LASTEXITCODE -ne 0) { throw "No se pudo iniciar Nginx para HTTP-01." }

$certificateName = if ($Staging) { "android.isicom.pt-staging" } else { "android.isicom.pt" }
$certbotArgs = @(
    "compose", "-f", $composeFile, "--profile", "certbot", "run", "--rm",
    "certbot", "certonly", "--webroot", "--webroot-path", "/var/www/certbot",
    "--domain", "android.isicom.pt", "--cert-name", $certificateName,
    "--email", $Email,
    "--agree-tos", "--no-eff-email", "--non-interactive"
)
if ($Staging) { $certbotArgs += "--staging" }

& docker @certbotArgs
if ($LASTEXITCODE -ne 0) {
    throw "Certbot no pudo validar android.isicom.pt. Comprueba el NAT/firewall del puerto 80."
}

if ($Staging) {
    Write-Host "Certificado de staging emitido. No se activó HTTPS de producción."
    exit 0
}

Copy-Item -LiteralPath $httpsTemplate -Destination $activeConfig -Force
docker compose -f $composeFile up -d --force-recreate nginx
if ($LASTEXITCODE -ne 0) { throw "El certificado se emitió, pero Nginx no pudo activar HTTPS." }
docker exec machining_nginx nginx -t
if ($LASTEXITCODE -ne 0) { throw "La configuración HTTPS de Nginx no es válida." }

Write-Host "HTTPS activado: https://android.isicom.pt/"
