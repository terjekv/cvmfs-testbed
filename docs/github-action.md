# GitHub Action and pull-request CI

The root of this repository is a JavaScript action with a post-job cleanup step.
Use `terjekv/cvmfs-testbed@main` during initial development, or pin a commit SHA.
The action runs the same controller and Compose file as the local CLI.

## Runner requirements

Use a Linux VM with Python 3, Bash, Docker Compose or rootful Podman with
`podman-compose` 1.5+, and permission to run privileged containers. The
repository's CI exercises both engines on `ubuntu-24.04` and
`ubuntu-24.04-arm`. The action selects the engine's native architecture.
Single-CPU `ubuntu-slim` runners cannot perform the required privileged mounts.

The job can use `pull_request` with `contents: read`. No cloud account, write
token, production signing key, or repository secret is needed. Avoid running
untrusted PR code on a persistent privileged self-hosted machine; standard
disposable GitHub-hosted VM runners fit this use case.

## Inputs

| Input | Default | Purpose |
| --- | --- | --- |
| `state-directory` | Unique directory under `runner.temp` | State, endpoint JSON, and public keys |
| `project-name` | Derived from the state directory | Optional explicit Compose project name |
| `cvmfs-version` | `2.14.1` | Exact CernVM-FS release series to install |
| `platform` | `auto` | `auto`, `linux/amd64`, or `linux/arm64` |
| `runtime` | `auto` | Prefer Docker then Podman; `docker` or `podman` selects explicitly |
| `sudo` | `false` | Run the engine/provider through `sudo -n`, for rootful Podman |
| `cleanup` | `true` | Remove containers and volumes in the post-job step |

Use separate state directories/project names for independent deployments. The
most recent successful action invocation sets the default CLI environment. For
multiple deployments, address each with `cvmfs-testbed --state-dir PATH ...`.

## Outputs and environment

| Output | Description |
| --- | --- |
| `state-directory` | Absolute directory containing controller state |
| `endpoints-file` | Absolute path to `endpoints.json` |
| `keys-directory` | Public signing keys |
| `network` | Container network name for consumers using the same engine |
| `s0-url`, `s1-url`, `s1-s3-url` | HTTP origins reachable from the runner host |
| `proxy-url` | Squid forward-proxy URL reachable from the runner host |

Subsequent steps get `cvmfs-testbed` on `PATH`, plus `CVMFS_TESTBED_STATE` and
`CVMFS_TESTBED_ENDPOINTS`. `endpoints.json` also contains `internal` URLs usable
from containers attached to `network`.

Call `cvmfs-testbed inspect` in any subsequent step for live service IPs, network
aliases, subnets, port bindings, and running/paused/health state. These are queried
at call time; the action outputs contain the stable connection URLs.

## Podman runners

Install Podman and `podman-compose` as described in [local setup](local.md#podman-on-linux),
then select them in the action:

```yaml
- uses: terjekv/cvmfs-testbed@main
  id: testbed
  with:
    runtime: podman
    sudo: 'true'
- run: cvmfs-testbed client-read software.testbed.test README.txt
```

The CLI remembers this choice, including during post-job cleanup. No Docker
installation or Docker socket is needed. Our [CI workflow](../.github/workflows/ci.yml)
includes the provider installation and exercises the same failure scenarios on
both runtimes. For containerized consumers below, use `sudo podman run` in place
of `docker run` when the testbed uses rootful Podman.

## Outage test in a PR

```yaml
name: Integration
on: [push, pull_request]
permissions:
  contents: read
jobs:
  live:
    runs-on: ubuntu-24.04
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v7
      - uses: terjekv/cvmfs-testbed@main
        id: testbed
      - name: Exercise outage
        run: |
          cvmfs-testbed stop s0
          trap 'cvmfs-testbed start s0' EXIT
          python3 - <<'PY'
          import json, subprocess
          report = json.loads(subprocess.check_output(['cvmfs-testbed', 'status']))
          assert not report['servers']['s0']['reachable']
          assert report['servers']['s1']['reachable']
          PY
          # Run your consumer against $CVMFS_TESTBED_ENDPOINTS here.
      - name: Collect diagnostics
        if: always()
        run: cvmfs-testbed logs --output artifacts/testbed
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: testbed-logs
          path: artifacts/testbed
```

Keep changes to the environment within one test sequence, or coordinate them
explicitly. Separate test processes should not assume a shared deployment stays
healthy while another test is stopping a server.

## Diagnostics when setup fails

The action records the state path before starting services and cleans up partial
deployments in its post-job step. Its PATH/environment outputs are only available
after successful setup. To upload diagnostics even when setup itself fails, use
an explicit state path and check out the harness at a known location:

```yaml
- uses: actions/checkout@v7
  with:
    repository: terjekv/cvmfs-testbed
    path: .testbed-action
- uses: ./.testbed-action
  with:
    state-directory: ${{ runner.temp }}/cvmfs-testbed
# Your test steps go here.
- name: Collect diagnostics
  if: always()
  env:
    CVMFS_TESTBED_STATE: ${{ runner.temp }}/cvmfs-testbed
  run: .testbed-action/bin/cvmfs-testbed logs --output artifacts/testbed
- uses: actions/upload-artifact@v7
  if: always()
  with:
    name: testbed-logs
    path: artifacts/testbed
```

The post-job step also collects logs under the state directory before cleanup,
but it runs after normal artifact-upload steps. Collect logs explicitly with
`if: always()` to preserve them as artifacts. Cleanup failures make the post-job
step fail visibly. `cleanup: 'false'` is primarily useful for debugging on a
runner you control; a hosted VM still disappears at the end of its job.

## Containerized consumers

```yaml
- name: Run a container on the test network
  env:
    TESTBED_NETWORK: ${{ steps.testbed.outputs.network }}
  run: |
    docker run --rm --network "$TESTBED_NETWORK" curlimages/curl:8.12.1 \
      --fail http://s1.testbed.test/cvmfs/software.testbed.test/.cvmfspublished
```

Host URLs use `127.0.0.1` and dynamically published ports. Container consumers
must use the `internal` URLs and join the named network. Reading manifests over
HTTP needs no mount privileges; only the real FUSE client requires them.

## Testing changes to the action

In this repository's PR workflows, checkout the proposed code and use `uses: ./`.
That tests the proposed action rather than downloading `main`. See
[the repository CI workflow](../.github/workflows/ci.yml).

Unit checks cover the controller and action cleanup. The integration jobs then
use the action to create a fresh deployment, run `tests/integration.py`, collect
logs on success or failure, and allow the action's post-job hook to remove it.
