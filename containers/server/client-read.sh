#!/usr/bin/env bash
set -euo pipefail
repo=${1:?repository required}
path=${2:-revision.txt}
case "$repo" in software.testbed.test|dev.testbed.test) ;; *) exit 2 ;; esac
mkdir -p /etc/cvmfs/config.d "/cvmfs/$repo"
cat > /etc/cvmfs/default.local <<'EOF'
CVMFS_CLIENT_PROFILE=single
CVMFS_CONFIG_REPOSITORY=
CVMFS_HTTP_PROXY=http://squid:3128
CVMFS_QUOTA_LIMIT=128
EOF
cat > "/etc/cvmfs/config.d/$repo.conf" <<EOF
CVMFS_SERVER_URL='http://s1.testbed.test/cvmfs/@fqrn@;http://s1-s3.testbed.test/cvmfs/@fqrn@'
CVMFS_PUBLIC_KEY=/public-keys/$repo.pub
EOF
mount -t cvmfs "$repo" "/cvmfs/$repo" >&2
trap 'umount "/cvmfs/$repo"' EXIT
cat "/cvmfs/$repo/$path"
