FROM postgres:16-alpine

RUN apk add --no-cache rsync

COPY live-backup.sh check-health.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/live-backup.sh /usr/local/bin/check-health.sh

ENTRYPOINT ["/usr/local/bin/live-backup.sh"]
