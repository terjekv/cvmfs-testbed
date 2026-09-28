# Related projects

There is substantial existing CernVM-FS deployment and test tooling. This project
packages a small environment for **downstream applications' integration tests**,
with the same control interface locally and in GitHub Actions.

## CernVM-FS upstream

[CernVM-FS's S3 integration stack](https://github.com/cvmfs/cvmfs/tree/devel/test/common/container/s3-integration)
combines the upstream development/test container with Garage object storage and
a one-shot initialization container. It is designed to build CernVM-FS from
source and run its server integration suite. Its native S3 configuration and
[test helpers](https://github.com/cvmfs/cvmfs/blob/devel/test/test_functions) are
useful references for real S3 replication.

The [publishing stack](https://github.com/cvmfs/cvmfs/tree/devel/test/common/container/publish)
provides a gateway, receiver, S3 store, and Apache frontend. The upstream tree also
contains a mountless publishing stack and tests for concurrent publishing.

Choose upstream tooling when developing CernVM-FS itself or testing gateway and
publisher internals. `cvmfs-testbed` instead installs release packages and exposes
separate S0, normal S1, S3 S1, and cache endpoints to applications under test.

## Other deployments

- [gabrielefronze/cvmfs-server-container](https://github.com/gabrielefronze/cvmfs-server-container)
  supplies a containerized S0, S1, client, helper commands, and a Squid example.
  It is close to this topology. At the September 2026 review its repository's
  last push was July 2019. It is useful prior art; its documented interface does
  not provide this project's S3 replica, action outputs, or per-job isolation.
- [WATonomous/cvmfs-ephemeral](https://github.com/WATonomous/cvmfs-ephemeral)
  focuses on an ephemeral S0 for CI artifacts, with upload, notification, and
  maintenance functionality.
- [galaxyproject/ansible-cvmfs](https://github.com/galaxyproject/ansible-cvmfs)
  configures clients, S0s, S1s, and local proxies. It is a broader configuration
  management option for installing infrastructure on managed machines.
- [cvmfs-contrib/github-action-cvmfs](https://github.com/cvmfs-contrib/github-action-cvmfs)
  installs/configures a client that mounts existing repositories. It complements
  a server testbed.

The value added here is the seeded two-repository topology, local/CI lifecycle,
machine-readable endpoints, controlled publication and replication, failure
scenarios, and cleanup. CernVM-FS remains responsible for repository creation,
signatures, catalogs, and replication. This project does not replace its server
implementation or test suite.

Comparison checked against the projects' public source and documentation on
2026-09-28. Links to `devel` describe upstream work that can change independently
of the release package used by this testbed.
