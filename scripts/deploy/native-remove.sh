#!/bin/bash
# Stop safely by default. --purge removes only this deployment after making a backup.
set -euo pipefail
BASE=/opt/kiber102
test "$(id -u)" = 0
test -d "$BASE"
test ! -L "$BASE"
test "$(readlink -f "$BASE")" = /opt/kiber102
UNITS=(kiber102.target kiber102-proxy.service kiber102-bot.service kiber102-worker.service kiber102-web.service kiber102-api.service)
systemctl disable --now "${UNITS[@]}"
if [[ "${1:-}" != --purge ]]; then
    echo 'KIBER102 stopped. Data retained in /opt/kiber102. Use --purge for backed-up removal.'
    exit 0
fi
BACKUP="/var/backups/kiber102-removal-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
umask 077
tar -czf "$BACKUP" -C "$BASE" app/.env app/.local Caddyfile deployment.json
tar -tzf "$BACKUP" >/dev/null
for unit in "${UNITS[@]}" kiber102.slice; do
    path="/etc/systemd/system/$unit"
    test ! -L "$path"
    rm -f -- "$path"
done
systemctl daemon-reload
# BASE is a fixed path, checked above. No shared runtimes or neighboring data are removed.
rm -rf --one-file-system -- "$BASE"
userdel kiber102
echo "KIBER102 removed. Private recovery backup: $BACKUP"
