#!/usr/bin/env bash
set -euo pipefail

operation=${1:?operation required}
repo=${2:?repository required}
case "$repo" in software.testbed.test|dev.testbed.test) ;; *) echo 'Unknown repository' >&2; exit 2 ;; esac

publish_content() {
  local message=${1:?} source=${2:-}
  cvmfs_server transaction "$repo"
  trap 'cvmfs_server abort -f "$repo" || true' ERR
  if [[ -n "$source" ]]; then cp -a "$source/." "/cvmfs/$repo/"; fi
  printf '%s\n%s\n' "$message" "$(cat /proc/sys/kernel/random/uuid)" > "/cvmfs/$repo/revision.txt"
  cvmfs_server publish "$repo"
  trap - ERR
  # Fail publication visibly if the resulting repository does not pass checking.
  # Bootstrap also runs check -a to initialize the native S0 status file.
  cvmfs_server check "$repo"
}

case "$operation" in
  create)
    [[ ! -e "/etc/cvmfs/repositories.d/$repo/server.conf" ]] || exit 0
    cvmfs_server mkfs -o root -f overlayfs -w "http://s0.testbed.test/cvmfs/$repo" "$repo"
    printf '\nCVMFS_REPOSITORY_TTL=5\n' >> "/etc/cvmfs/repositories.d/$repo/server.conf"
    cp "/etc/cvmfs/keys/$repo.pub" /public-keys/
    mkdir -p /tmp/testbed-seed/nested
    printf 'CernVM-FS testbed repository: %s\n' "$repo" > /tmp/testbed-seed/README.txt
    printf '#!/bin/sh\nprintf "hello from the testbed\\n"\n' > /tmp/testbed-seed/hello.sh
    chmod +x /tmp/testbed-seed/hello.sh
    touch /tmp/testbed-seed/nested/.cvmfscatalog
    printf 'Nested catalog content\n' > /tmp/testbed-seed/nested/example.txt
    publish_content 'Initial testbed publication' /tmp/testbed-seed
    ;;
  add-replica)
    [[ ! -e "/etc/cvmfs/repositories.d/$repo/server.conf" ]] || exit 0
    options=(-o root -a)
    if [[ "${TESTBED_ROLE:?}" == s1-s3 ]]; then
      options+=(-s /etc/cvmfs/testbed-s3.conf -w http://s1-s3.testbed.test/cvmfs)
    fi
    cvmfs_server add-replica "${options[@]}" "http://s0.testbed.test/cvmfs/$repo" "/public-keys/$repo.pub"
    ;;
  publish) publish_content "${3:-Testbed publication}" "${4:-}" ;;
  replicate) cvmfs_server snapshot "$repo" ;;
  *) echo 'Unknown repository operation' >&2; exit 2 ;;
esac
