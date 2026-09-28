FROM docker.io/library/ubuntu:24.04

ARG CVMFS_VERSION=2.14.1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg apache2 libapache2-mod-wsgi-py3 \
      python3 python3-boto3 squid fuse3 attr procps psmisc uuid-runtime \
    && curl -fsSL https://cvmrepo.s3.cern.ch/cvmrepo/apt/cvmfs-release-latest_all.deb \
       -o /tmp/cvmfs-release.deb \
    && dpkg -i /tmp/cvmfs-release.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       "cvmfs=${CVMFS_VERSION}*" "cvmfs-server=${CVMFS_VERSION}*" \
    && rm -rf /var/lib/apt/lists/* /tmp/cvmfs-release.deb \
    && rm -f /usr/bin/systemctl /bin/systemctl \
    && a2enmod headers expires proxy proxy_http wsgi \
    && printf 'ServerName localhost\n' > /etc/apache2/conf-available/testbed.conf \
    && a2enconf testbed \
    && mkdir -p /public-keys /etc/cvmfs/repositories.d /var/spool/cvmfs /srv/cvmfs \
    && printf 'CVMFS_GEO_DB_FILE=NONE\nCVMFS_GEO_AUTO_UPDATE=false\n' > /etc/cvmfs/server.local \
    && printf 'CVMFS_CONFIG_REPOSITORY=\nCVMFS_HTTP_PROXY=DIRECT\nCVMFS_QUOTA_LIMIT=128\n' > /etc/cvmfs/default.local

COPY containers/server/ /opt/testbed/
COPY containers/squid/squid.conf /etc/squid/squid.conf
COPY containers/s3/cvmfs-s3.conf /etc/cvmfs/testbed-s3.conf
RUN chmod +x /opt/testbed/*.sh
ENTRYPOINT ["/opt/testbed/entrypoint.sh"]
