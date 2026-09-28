#!/usr/bin/env bash
set -euo pipefail

case "${TESTBED_ROLE:?}" in
  squid)
    mkdir -p /var/spool/squid /var/log/squid /run/squid
    chown -R proxy:proxy /var/spool/squid /var/log/squid /run/squid
    squid -N -z
    exec squid -N -d 1
    ;;
  s1-s3) exec sleep infinity ;;
  s0|s1)
    # cvmfs_server uses the distribution's service command for Apache reloads.
    rm -f /run/testbed-ready
    rm -f /var/run/apache2/apache2.pid
    service apache2 start
    if [[ "$TESTBED_ROLE" == s0 ]] && compgen -G '/etc/cvmfs/repositories.d/*/server.conf' > /dev/null; then
      cvmfs_server mount -a
    fi
    touch /run/testbed-ready
    shutdown() {
      rm -f /run/testbed-ready
      service apache2 stop || true
      exit 0
    }
    trap shutdown TERM INT
    while true; do
      sleep 2 &
      wait "$!" || true
      service apache2 status > /dev/null || exit 1
    done
    ;;
  *) echo 'Unknown testbed role' >&2; exit 2 ;;
esac
