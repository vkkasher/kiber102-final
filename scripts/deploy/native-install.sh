#!/bin/bash
# Dedicated, removable temporary Ubuntu deployment. Run only on the owner's host.
set -euo pipefail
umask 027
BASE=/opt/kiber102
test "$(id -u)" = 0
test "$(readlink -f "$BASE")" = /opt/kiber102
test -f "$BASE/app/requirements.lock.txt"
test -f "$BASE/app/.env"
test ! -f "$BASE/INSTALLED"
getent passwd kiber102 >/dev/null || useradd --system --home-dir "$BASE" --shell /usr/sbin/nologin kiber102
install -d -o root -g kiber102 -m 750 "$BASE" "$BASE/runtime" "$BASE/backups"
install -d -o kiber102 -g kiber102 -m 750 "$BASE/proxy-data" "$BASE/proxy-config" "$BASE/build-home"
chown -R root:kiber102 "$BASE/app"
chmod 750 "$BASE/app"
chmod 640 "$BASE/app/.env"
chown -R kiber102:kiber102 "$BASE/app/.local"
python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$BASE/app/requirements.lock.txt"
chown -R root:kiber102 "$BASE/venv"
chmod -R g+rX "$BASE/venv"
cd "$BASE/runtime"
curl --fail --silent --show-error --location https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt -o SHASUMS256.txt
NODE_ARCHIVE=$(awk '/ node-v[0-9.]+-linux-x64.tar.xz$/ {print $2; exit}' SHASUMS256.txt)
test -n "$NODE_ARCHIVE"
curl --fail --silent --show-error --location "https://nodejs.org/dist/latest-v24.x/$NODE_ARCHIVE" -o "$NODE_ARCHIVE"
grep " $NODE_ARCHIVE$" SHASUMS256.txt | sha256sum --check
mkdir node
tar -xJf "$NODE_ARCHIVE" -C node --strip-components=1
# Ubuntu signed repository package, extracted privately without adding a global service.
apt-get download caddy
dpkg-deb -x caddy_*_amd64.deb caddy-package
install -m 755 caddy-package/usr/bin/caddy "$BASE/runtime/caddy"
chown -R root:kiber102 "$BASE/runtime"
chmod -R g+rX "$BASE/runtime"
chown -R kiber102:kiber102 "$BASE/app/apps/web"
cd "$BASE/app/apps/web"
runuser -u kiber102 -- env HOME="$BASE/build-home" PATH="$BASE/runtime/node/bin:/usr/bin:/bin" npm ci --no-audit --no-fund
runuser -u kiber102 -- env HOME="$BASE/build-home" PATH="$BASE/runtime/node/bin:/usr/bin:/bin" WEB_PUBLIC_URL="${KIBER102_PUBLIC_URL:-https://kiber102.vkkasher.ru}" NODE_OPTIONS=--max-old-space-size=1152 NEXT_TELEMETRY_DISABLED=1 npm run build
cp -a public .next/standalone/
cp -a .next/static .next/standalone/.next/
chown -R root:kiber102 "$BASE/app/apps/web"
install -d -o kiber102 -g kiber102 -m 750 "$BASE/app/apps/web/.next/standalone/.next/cache"
touch "$BASE/INSTALLED"
echo 'BUILD_READY'
