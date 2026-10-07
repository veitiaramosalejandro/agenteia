$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $projectRoot "docker-compose-prod.yml"
Set-Location $projectRoot

docker compose -f $composeFile --profile certbot run --rm certbot renew --webroot --webroot-path /var/www/certbot --quiet
if ($LASTEXITCODE -ne 0) { throw "Falló la renovación de Let's Encrypt." }

docker exec machining_nginx nginx -s reload
if ($LASTEXITCODE -ne 0) { throw "El certificado se renovó, pero Nginx no pudo recargarlo." }
