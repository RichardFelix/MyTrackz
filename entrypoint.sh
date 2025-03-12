#!/bin/sh

set -e

python manage.py migrate --noinput

PUID=${PUID:-1000}
PGID=${PGID:-1000}

if [ "$(id -u abc)" != "$PUID" ] || [ "$(id -g abc)" != "$PGID" ]; then
    # Delete and recreate the user with the correct IDs
    deluser abc
    addgroup -g "$PGID" abc
    adduser -D -H -G abc -s /bin/sh -u "$PUID" abc
fi

chown -R abc:abc /yamtrack
chown -R abc:abc db
chown -R abc:abc staticfiles

exec /usr/local/bin/supervisord -c /etc/supervisord.conf