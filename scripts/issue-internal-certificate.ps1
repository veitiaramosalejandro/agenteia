param(
    [switch]$ForceNewCA
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose-prod.yml"
$certificateDir = Join-Path $projectRoot "certbot\internal"
$httpsTemplate = Join-Path $projectRoot "nginx\templates\agent-internal-https.conf"
$activeConfig = Join-Path $projectRoot "nginx\conf.d\agent.conf"

Set-Location $projectRoot
New-Item -ItemType Directory -Force -Path $certificateDir | Out-Null

$caKey = Join-Path $certificateDir "isicom-internal-ca.key"
$caCertificate = Join-Path $certificateDir "isicom-internal-ca.crt"

function Invoke-OpenSsl {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & docker run --rm -v "${certificateDir}:/certs" alpine/openssl @Arguments
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL terminó con error." }
}

if ($ForceNewCA -or -not (Test-Path $caKey) -or -not (Test-Path $caCertificate)) {
    if ($ForceNewCA) {
        Remove-Item -LiteralPath $caKey, $caCertificate -Force -ErrorAction SilentlyContinue
    }
    Invoke-OpenSsl genrsa -out /certs/isicom-internal-ca.key 4096
    Invoke-OpenSsl req -x509 -new -sha256 -days 3650 `
        -key /certs/isicom-internal-ca.key `
        -out /certs/isicom-internal-ca.crt `
        -subj "/C=PT/O=ISICOM/OU=Infrastructure/CN=ISICOM Internal Root CA"
}

Invoke-OpenSsl genrsa -out /certs/android.isicom.pt.key 3072
Invoke-OpenSsl req -new `
    -key /certs/android.isicom.pt.key `
    -out /certs/android.isicom.pt.csr `
    -config /certs/openssl.cnf
Invoke-OpenSsl x509 -req -sha256 -days 825 `
    -in /certs/android.isicom.pt.csr `
    -CA /certs/isicom-internal-ca.crt `
    -CAkey /certs/isicom-internal-ca.key `
    -CAcreateserial `
    -out /certs/android.isicom.pt.crt `
    -extfile /certs/openssl.cnf `
    -extensions v3_req

Copy-Item -LiteralPath $httpsTemplate -Destination $activeConfig -Force
docker compose -f $composeFile up -d --force-recreate nginx
if ($LASTEXITCODE -ne 0) { throw "No se pudo activar Nginx con el certificado interno." }
docker exec machining_nginx nginx -t
if ($LASTEXITCODE -ne 0) { throw "La configuración HTTPS de Nginx no es válida." }

Write-Host "Certificado interno activado: https://android.isicom.pt/"
Write-Host "Instala como raíz de confianza este archivo en cada cliente:"
Write-Host "  $caCertificate"
Write-Warning "Protege isicom-internal-ca.key: es la clave privada de la CA interna."
