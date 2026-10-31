#!/bin/sh
# squid demands a certificate on an ssl-bump port even when it only splices (no interception).
# Generate a throwaway one into tmpfs at every start; nothing is committed or persisted.
set -eu
mkdir -p /tmp/ssl
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=a4-egress-splice-only \
    -keyout /tmp/ssl/bump.key -out /tmp/ssl/bump.crt 2>/dev/null
cat /tmp/ssl/bump.crt /tmp/ssl/bump.key > /tmp/ssl/bump.pem
rm -f /tmp/ssl/bump.key /tmp/ssl/bump.crt
exec squid -N -f /etc/squid/squid.conf
