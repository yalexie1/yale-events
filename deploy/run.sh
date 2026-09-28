#!/bin/sh
# Container entrypoint: serves the API and scrapes every $SCRAPE_EVERY_HOURS (default 4).
# data/ is a persistent volume; data/last-scrape keeps restarts and redeploys from scraping early.
set -u
mkdir -p data/logs

scrape_loop() {
  interval=$(( ${SCRAPE_EVERY_HOURS:-4} * 3600 ))
  while true; do
    last=$(stat -c %Y data/last-scrape 2>/dev/null || echo 0)
    wait=$(( last + interval - $(date +%s) ))
    if [ "$wait" -gt 0 ]; then sleep "$wait"; fi
    yev scrape || echo "scrape exited with $?" >&2
    touch data/last-scrape
    yev sources check || true
  done
}

scrape_loop &
exec yev serve --host 0.0.0.0 --port 8080
