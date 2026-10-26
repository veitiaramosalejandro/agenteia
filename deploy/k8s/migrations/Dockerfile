FROM postgres:15-alpine

COPY database/init/ /migrations/
COPY deploy/k8s/migrations/run-migrations.sh /usr/local/bin/run-migrations.sh

RUN chmod 0755 /usr/local/bin/run-migrations.sh

ENTRYPOINT ["/usr/local/bin/run-migrations.sh"]

