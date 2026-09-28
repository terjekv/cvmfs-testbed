#!/bin/sh
set -eu
# Docker and Podman supply different embedded DNS resolver addresses.
resolver=$(awk '$1 == "nameserver" { print $2; exit }' /etc/resolv.conf)
test -n "$resolver"
case "$resolver" in *:*) resolver="[$resolver]" ;; esac
sed "s/TESTBED_DNS_RESOLVER/$resolver/" /etc/nginx/testbed.conf.template > /etc/nginx/conf.d/default.conf
exec nginx -g 'daemon off;'
