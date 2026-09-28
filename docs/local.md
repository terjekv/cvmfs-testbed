# Local use

## Requirements and scope

Use a Linux Docker engine with Compose v2+, or rootful Podman 4.9+ with
`podman-compose` 1.5+, plus Python 3.9+ and Bash. Docker Desktop
provides the Linux engine on macOS. The server automatically uses the engine's
native amd64/arm64 architecture; the host Python architecture is irrelevant.
Use a normal Linux VM runner in CI, such as `ubuntu-24.04`, rather than an
unprivileged container runner.

The S0 and disposable client containers are privileged because they mount FUSE
and OverlayFS. Repository scratch and storage live in container named volumes on
the Linux engine, avoiding macOS bind-mounted scratch filesystems. Host CernVM-FS
configuration and mounts are not changed.

Only the S0, two replica HTTP endpoints, and Squid have published ports. These
bind to `127.0.0.1` and use dynamically allocated ports. The object-store API is
on the deployment's container network. Treat this as a disposable development
environment; it uses documented fixture credentials and is not a production
deployment recipe.

Ports are selected from free localhost ports during setup and then explicitly
bound, so endpoint URLs stay stable across stop/start operations. If another
process claims a selected port before the engine binds it, setup fails visibly;
collect diagnostics, run `down`, and retry `up`. A new deployment may use new ports.

## Podman on Linux

Install Podman and the standalone `podman-compose` provider. Docker and Docker
Compose are not required for this path. For Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install -y podman python3-venv
sudo python3 -m venv /opt/testbed-podman-compose
sudo /opt/testbed-podman-compose/bin/pip install podman-compose==1.5.0
sudo ln -s /opt/testbed-podman-compose/bin/podman-compose /usr/local/bin/podman-compose

./bin/cvmfs-testbed up --runtime podman --sudo
./bin/cvmfs-testbed inspect
./bin/cvmfs-testbed down
```

`--sudo` uses `sudo -n` for engine/provider commands only; arrange noninteractive
sudo access or refresh your sudo session before starting. When already running as
root, omit this option. All later commands read the engine and sudo setting from
state. `auto` tries an available Docker engine before Podman; explicit selection
never silently switches engines. Rootless Podman is rejected because the real
publisher requires privileged FUSE and OverlayFS mounts. Podman Machine on macOS
and remote engines are not currently validated configurations.

Every service has its own network namespace: the provider is run with
`--in-pod false`. Nginx obtains its resolver from the container's `resolv.conf`,
so object-store restarts work with either engine's DNS. Bind-mounted fixture
configuration uses shared SELinux labels (`:z`).

## State and multiple deployments

Commands default to `.cvmfs-testbed` in the current working directory. Either
set the environment variable or pass the global option before the command:

```bash
export CVMFS_TESTBED_STATE="$PWD/.cvmfs-testbed"
cvmfs-testbed up
cvmfs-testbed --state-dir /tmp/another-testbed up --project-name another-testbed
```

Each state directory holds:

- `state.json`: deployment ownership, runtime, sudo setting, platform, and CernVM-FS version;
- `runtime.env`: Compose interpolation settings;
- `endpoints.json`: URLs, network, repository names, and public-key paths;
- `keys/`: public signing keys copied from S0;
- `.lock`: prevents concurrent mutations by test processes.

Do not delete state while its deployment exists: it is needed to control and
remove the containers. `up` refuses to overwrite existing deployment state or
take over an existing Compose project. Setup failures retain state for diagnosis.
Use `logs`, then `down`, then `up` to retry with fresh repositories.

`stop`/`start` preserve the containers and their repository configuration. A
publisher restart remounts its repositories before it can publish again. `down`
removes containers and named volumes; a subsequent `up` creates fresh keys and
repository history. Build images remain cached. Exported log directories remain.

## Supply content

```bash
mkdir -p /tmp/sample-content/bin
printf '#!/bin/sh\necho testbed\n' > /tmp/sample-content/bin/example
chmod +x /tmp/sample-content/bin/example
cvmfs-testbed publish software.testbed.test --source /tmp/sample-content
cvmfs-testbed replicate all software.testbed.test
cvmfs-testbed client-read software.testbed.test bin/example
```

`--source` merges a directory into the repository. It does not remove files that
are absent from the source. Every publication updates `revision.txt` with the
message and a fresh identifier so repeated identical publications still advance
the repository. The initial seed includes `README.txt`, `hello.sh`,
`revision.txt`, and `nested/example.txt` in a nested catalog.

## Connect from your own code

```python
import json
import os
from pathlib import Path

state = Path(os.environ.get('CVMFS_TESTBED_STATE', '.cvmfs-testbed'))
endpoints = json.loads((state / 'endpoints.json').read_text())
s0_url = endpoints['host']['s0']
repositories = endpoints['repositories']
```

Use `host` URLs for host processes. Use `internal` URLs after joining the
deployment's container network. A URL containing `127.0.0.1` points at the calling
container itself when used inside a container. The `.test` names are container
network aliases, not host DNS names.

The optional `client-read` starts a new client container on each invocation. It
mounts a repository using the test public key, fetches through Squid, prints the
file, unmounts, and removes the client container. Fresh clients avoid persistent
client-cache state interfering with revision tests.

## Query live IP addresses and service state

```bash
cvmfs-testbed inspect
# Optional jq examples:
cvmfs-testbed inspect | jq '.services.s0.networks'
cvmfs-testbed inspect | jq '.services.s1.published_ports'
cvmfs-testbed inspect | jq '.networks'
```

`inspect` queries the selected engine each time and includes its `runtime` name.
Its `services` object is keyed by service
name and includes container ID/name, hostname, running/paused/health state, exit
code, network IPv4/IPv6 addresses, gateway addresses, DNS aliases, and current
published port mappings. Its `networks` object includes network IDs, drivers, and
IPAM subnet/gateway settings. Unassigned IP addresses are `null`; stopped
containers can have no active addresses or port mappings.

Container addresses may change across restarts, and Docker Desktop's bridge IPs
normally cannot be reached directly from the macOS host. Use `endpoints.host`
from host tests, and internal DNS aliases from containers. The host URLs remain
stable for the lifetime of the deployment. `inspect` is for discovering current
network state, not a promise that a container IP is globally routable.

Read the same data from a test:

```python
import json
import subprocess

info = json.loads(subprocess.check_output(['cvmfs-testbed', 'inspect']))
s0 = info['services']['s0']
assert s0['running'] and not s0['paused']
addresses = [network['ipv4'] for network in s0['networks'].values()]
```

## Troubleshooting

```bash
cvmfs-testbed logs --output artifacts/testbed
cvmfs-testbed exec s0 -- cvmfs_server list
cvmfs-testbed exec s1 -- cat /var/log/cvmfs/snapshots.log
cvmfs-testbed exec squid -- cat /var/log/squid/access.log
```

If the engine cannot mount FUSE or OverlayFS, check that it permits privileged
containers and supports those Linux filesystems. The publisher scratch directory
must remain on the container volume. On Apple Silicon, leave `--platform` at `auto`;
emulated amd64 processes can exhaust the single-digit file descriptors used by
CernVM-FS shell locks.

Image builds require access to Ubuntu, CERN package repositories, and container
registries. Once the images are built, the services do not need production
repositories, a public GeoIP download, or cloud services.
