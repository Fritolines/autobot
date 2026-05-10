FROM alpine:3.20

RUN apk add --no-cache docker-cli curl

COPY notify.sh /notify.sh
RUN chmod +x /notify.sh

ENTRYPOINT ["/bin/sh", "/notify.sh"]
