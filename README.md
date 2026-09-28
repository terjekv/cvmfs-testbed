# cvmfs-testbed

A disposable CernVM-FS deployment for development and integration tests, usable
locally and as a GitHub Action. It runs a real publisher, a local-storage replica,
an S3-backed replica, and a Squid cache. Tests control when publication and
replication happen and can stop, restart, or freeze individual services.

Every deployment creates fresh signing keys and two small repositories:

- `software.testbed.test`
- `dev.testbed.test`

There are no dependencies on a production CernVM-FS deployment. The repository
names are fixture identities; they require no public DNS registration.

For existing CERN and community tooling, see [related projects](docs/related-projects.md).

## Run locally

Requirements: Python 3.9+, Bash, Docker with Compose v2 or newer, and a Linux
Docker engine that permits privileged containers and FUSE/OverlayFS mounts.
Linux hosts and Docker Desktop on macOS are supported paths. Native amd64 and
arm64 images are selected automatically. Use native containers: x86 emulation on
Apple Silicon can break the publisher's file-descriptor locking.

```bash
git clone https://github.com/terjekv/cvmfs-testbed.git
cd cvmfs-testbed
./bin/cvmfs-testbed up
./bin/cvmfs-testbed status
./bin/cvmfs-testbed inspect
./bin/cvmfs-testbed client-read software.testbed.test README.txt
```

The first run builds the server image and downloads its packages. Later runs use
Docker's build cache. `up` prints JSON with the dynamically assigned localhost
URLs, the Docker network name, container URLs, repository names, and public key
directory. The same data is saved in `.cvmfs-testbed/endpoints.json`.

```bash
# Publish on S0, then update only the normal S1. The S3 S1 stays behind.
./bin/cvmfs-testbed publish software.testbed.test --message 'new revision'
./bin/cvmfs-testbed replicate s1 software.testbed.test
./bin/cvmfs-testbed status --json

# Recover the S3 replica.
./bin/cvmfs-testbed replicate s1-s3 software.testbed.test
./bin/cvmfs-testbed wait --synced

# Both replicas keep serving published content while S0 is stopped.
./bin/cvmfs-testbed stop s0
./bin/cvmfs-testbed client-read software.testbed.test README.txt
./bin/cvmfs-testbed start s0
./bin/cvmfs-testbed wait --synced

# Collect diagnostics, then remove this deployment and its volumes.
./bin/cvmfs-testbed logs --output artifacts/testbed
./bin/cvmfs-testbed down
```

Keep the CLI available with `export PATH="$PWD/bin:$PATH"`. To use it from another
working directory, set `CVMFS_TESTBED_STATE` to the absolute state directory.
Independent state directories and Compose project names allow multiple
deployments. See [local use and troubleshooting](docs/local.md).

## Use in a pull-request workflow

```yaml
name: Live integration tests
on: [push, pull_request]
permissions:
  contents: read
jobs:
  integration:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - uses: terjekv/cvmfs-testbed@main # Pin a commit SHA for reproducible CI.
        id: testbed
        with:
          state-directory: ${{ runner.temp }}/cvmfs-testbed
      - name: Run project tests
        env:
          S0_URL: ${{ steps.testbed.outputs.s0-url }}
          S1_URL: ${{ steps.testbed.outputs.s1-url }}
          S3_URL: ${{ steps.testbed.outputs.s1-s3-url }}
        run: |
          cvmfs-testbed status
          # Replace this probe with your project's integration-test command.
          curl --fail "$S0_URL/cvmfs/software.testbed.test/.cvmfspublished"
      - name: Collect diagnostics
        if: always()
        run: cvmfs-testbed logs --output artifacts/testbed
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: testbed-logs
          path: artifacts/testbed
```

The action adds `cvmfs-testbed` to `PATH` and exports `CVMFS_TESTBED_STATE` and
`CVMFS_TESTBED_ENDPOINTS`. Its post-job step removes containers and volumes,
including after a setup failure. No cloud credentials or repository secrets are
needed. Normal `pull_request` workflows can use it.

See [the action interface and CI examples](docs/github-action.md), including how
to collect diagnostics when setup itself fails and how to run tests in containers.
`@main` is available during initial development; no `v1` release is implied.

## Control the environment

| Command | Effect |
| --- | --- |
| `up` | Build, create, seed, and synchronize a fresh deployment |
| `status` | JSON report of repository HTTP reachability and revision/catalog equality |
| `endpoints` | JSON connection information for host and container consumers |
| `inspect` | Live container IPs, DNS aliases, published ports, network subnets, and running/paused/health state |
| `publish REPOSITORY [--source DIR] [--message TEXT]` | Publish a new revision on S0; optionally merge your own files |
| `replicate s1\|s1-s3\|all [REPOSITORY]` | Snapshot the chosen replica(s), for one or both repositories |
| `stop SERVICE` / `start SERVICE` | Stop/restart while preserving repository state |
| `pause SERVICE` / `resume SERVICE` | Freeze/unfreeze processes, useful for timeout tests |
| `wait [--synced] [--timeout SECONDS]` | Wait for readable repositories, optionally with matching revisions and catalogs |
| `client-read REPOSITORY [PATH]` | Read through a fresh FUSE client and Squid |
| `exec SERVICE -- COMMAND ...` | Run a diagnostic command inside a container |
| `logs [--output DIR]` | Export service logs and deployment information |
| `down` | Remove the deployment and its data volumes; safe to repeat |

Services are `s0`, `s1`, `s1-s3`, `object-store`, and `squid`. Controlling
`s1-s3` controls its HTTP endpoint and replication worker together. `exec` also
accepts `s1-s3-worker` for diagnosis. **There is no background replication**:
staleness lasts until your test calls `replicate`.

`status` exits successfully when it can produce a report, including reports of
outages. Assert its JSON fields in your tests. `wait` returns a nonzero exit code
when its deadline expires. `healthy` covers the three repository HTTP endpoints;
it is not a general health check for Squid or the replication worker.

See [fault scenarios and test integration](docs/scenarios.md).

## What runs behind the scenes

The deployment uses upstream `cvmfs_server mkfs`, `transaction`, `publish`,
`add-replica`, and `snapshot`. Manifests, catalogs, signatures, and repository
status files come from CernVM-FS itself. S3 replication uses the real CernVM-FS S3
backend against a local SeaweedFS service. Nginx exposes its objects at the
standard CernVM-FS paths. Squid is a forward proxy for clients.

The default package version is CernVM-FS 2.14.1. Other versions can be requested
with `up --cvmfs-version X.Y.Z` or the action input, subject to package availability
and compatibility. See [architecture, data flow, and limitations](docs/architecture.md).

## Develop this repository

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/action.test.js
shellcheck bin/cvmfs-testbed containers/server/*.sh
actionlint

./bin/cvmfs-testbed up
python3 tests/integration.py
./bin/cvmfs-testbed down
```

The integration suite changes repository content, tests selective replication,
reads through FUSE, verifies a Squid cache hit, and stops/restarts the publisher,
both replica endpoints, and object storage. It needs no production services.
