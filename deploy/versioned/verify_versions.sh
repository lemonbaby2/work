#!/usr/bin/env bash
set -euo pipefail

host="${1:-127.0.0.1}"
for port in 8096 8099 8101 8102 8103 8104 8105; do
  status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "http://${host}:${port}/")"
  printf '%s:%s HTTP %s\n' "$host" "$port" "$status"
  test "$status" = "200"
done

if test "$host" = "127.0.0.1"; then
  cvat_status="$(curl --silent --show-error --header 'Host: localhost' --output /dev/null --write-out '%{http_code}' --max-time 10 "http://${host}:8081/api/server/about")"
else
  cvat_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' --max-time 10 "http://${host}:8081/api/server/about")"
fi
printf '%s:8081 CVAT API HTTP %s\n' "$host" "$cvat_status"
test "$cvat_status" = "200"
