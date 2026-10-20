FROM postgres:15-alpine

COPY database/init/ /migrations/
COPY deploy/k8s/overlays/docker-desktop-local/run-migrations.sh /usr/local/bin/run-migrations.sh
RUN chmod +x /usr/local/bin/run-migrations.sh

ENTRYPOINT ["/usr/local/bin/run-migrations.sh"]
