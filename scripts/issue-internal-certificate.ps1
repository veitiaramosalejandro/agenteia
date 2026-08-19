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
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & docker run --rm -v "${certificateDir}:/certs" alpine/openssl @Arguments
    if ($LASTEXITCODE -ne 0) { throw "OpenSSL terminó con error." }
}

if ($ForceNewCA -or -not (Test-Path $caKey) -or -not (Test-Path $caCertificate)) {
    if ($ForceNewCA) {
        Remove-Item -LiteralPath $caKey, $caCertificate -Force -ErrorAction SilentlyContinue
    }
    Invoke-OpenSsl -Arguments @(
        "genrsa", "-out", "/certs/isicom-internal-ca.key", "4096"
    )
    Invoke-OpenSsl -Arguments @(
        "req", "-x509", "-new", "-sha256", "-days", "3650",
        "-key", "/certs/isicom-internal-ca.key",
        "-out", "/certs/isicom-internal-ca.crt",
        "-subj", "/C=PT/O=ISICOM/OU=Infrastructure/CN=ISICOM Internal Root CA"
    )
}

Invoke-OpenSsl -Arguments @(
    "genrsa", "-out", "/certs/android.isicom.pt.key", "3072"
)
Invoke-OpenSsl -Arguments @(
    "req", "-new",
    "-key", "/certs/android.isicom.pt.key",
    "-out", "/certs/android.isicom.pt.csr",
    "-config", "/certs/openssl.cnf"
)
Invoke-OpenSsl -Arguments @(
    "x509", "-req", "-sha256", "-days", "825",
    "-in", "/certs/android.isicom.pt.csr",
    "-CA", "/certs/isicom-internal-ca.crt",
    "-CAkey", "/certs/isicom-internal-ca.key",
    "-CAcreateserial",
    "-out", "/certs/android.isicom.pt.crt",
    "-extfile", "/certs/openssl.cnf",
    "-extensions", "v3_req"
)

if (Test-Path $httpsTemplate) {
    Copy-Item -LiteralPath $httpsTemplate -Destination $activeConfig -Force
} else {
    # Fallback autosuficiente para despliegues donde solo se copió el script.
    $internalHttpsConfig = @'
upstream solidset_agent_api {
    server agent-service:8000;
    keepalive 32;
}

server {
    listen 80;
    listen [::]:80;
    server_name android.isicom.pt;

    location = /nginx-health {
        access_log off;
        default_type text/plain;
        return 200 "ok\n";
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name android.isicom.pt;

    ssl_certificate /etc/nginx/internal/android.isicom.pt.crt;
    ssl_certificate_key /etc/nginx/internal/android.isicom.pt.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    client_max_body_size 50m;

    location = /nginx-health {
        access_log off;
        default_type text/plain;
        return 200 "ok\n";
    }

    location / {
        proxy_pass http://solidset_agent_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_connect_timeout 15s;
        proxy_send_timeout 900s;
        proxy_read_timeout 900s;
        proxy_buffering off;
        proxy_request_buffering off;
    }
}
'@
    $activeDirectory = Split-Path -Parent $activeConfig
    New-Item -ItemType Directory -Force -Path $activeDirectory | Out-Null
    Set-Content -LiteralPath $activeConfig -Value $internalHttpsConfig -Encoding utf8
}
docker compose -f $composeFile up -d --force-recreate nginx
if ($LASTEXITCODE -ne 0) { throw "No se pudo activar Nginx con el certificado interno." }
docker exec machining_nginx nginx -t
if ($LASTEXITCODE -ne 0) { throw "La configuración HTTPS de Nginx no es válida." }

Write-Host "Certificado interno activado: https://android.isicom.pt/"
Write-Host "Instala como raíz de confianza este archivo en cada cliente:"
Write-Host "  $caCertificate"
Write-Warning "Protege isicom-internal-ca.key: es la clave privada de la CA interna."
