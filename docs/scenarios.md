# Fault scenarios and test integration

The CLI is intended to be called by tests. Commands return only their result on
stdout; container engine and CernVM-FS progress is written to stderr. `status` and
`endpoints` always emit JSON. Mutating commands fail visibly and do not retry a
failed publication behind the test's back.

## S0 outage

```bash
cvmfs-testbed stop s0
./your-monitoring-test --expect-s0-down
cvmfs-testbed start s0
cvmfs-testbed wait --synced
```

Already replicated content remains readable from both S1s. New publication and
replication from S0 fail while it is stopped. Recovery preserves repository
history and signing keys.

## One S1 behind

```bash
cvmfs-testbed publish software.testbed.test
cvmfs-testbed replicate s1 software.testbed.test
./your-monitoring-test --expect-s3-behind
cvmfs-testbed replicate s1-s3 software.testbed.test
cvmfs-testbed wait --synced
```

Reverse `s1` and `s1-s3` to leave the normal replica behind. Omit the repository
argument to replicate both repositories. Stopping a replica is unnecessary to
create lag: no cron jobs or background snapshot loops run.

## Programmatic control

```python
import json
import subprocess

def command(*args):
    return subprocess.check_output(['cvmfs-testbed', *args], text=True)

try:
    command('stop', 's0')
    state = json.loads(command('status'))
    assert not state['servers']['s0']['reachable']
    assert state['servers']['s1']['reachable']
    # Run your scraper/status generator here and assert its own outputs.
finally:
    command('start', 's0')
command('wait', '--synced')
```

`healthy` means both repositories' manifests and status files were readable at
all three server endpoints. `synced` additionally compares revisions and root
catalog hashes. Signature authenticity is tested by the real CernVM-FS client;
the CLI's small manifest parser does not verify signatures.

## Other failures

| Operation | Observable effect |
| --- | --- |
| `stop s1` | The normal S1 endpoint becomes unavailable |
| `stop s1-s3` | S3 HTTP endpoint and its replication worker stop together |
| `stop object-store` | S3 HTTP requests fail while its frontend keeps running |
| `stop squid` | Fresh client requests through the proxy fail; direct scrapes can still succeed |
| `pause s0` | The publisher freezes; HTTP clients encounter stalled connections/timeouts |
| `resume s0` | Unfreeze it, retaining state |

Always restore a paused service with `resume` before further operations. Use
`try/finally` in tests so the next scenario starts in a known state. `start`
does not replicate; recovery of an outdated replica still requires `replicate`.

For timeout tests, configure reasonable deadlines in your consumer. A missing
HTTP service and a frozen process are different failure modes. `status` probes
each request with a short timeout; its total duration can include several probes.

## Caching

To exercise Squid, send requests to the internal S1 hostname through the proxy's
host URL, or use `client-read`. A second read through the same mounted client can
be served from the client's local disk, so it alone does not prove a Squid hit.
The integration suite requests a content-addressed catalog object twice and
checks Squid's access log for `TCP_HIT`/`TCP_MEM_HIT`.

Direct scraper requests intentionally bypass Squid. The fixture's Squid also
does not cache mutable manifests, whitelist, repository status, or discovery
metadata: this makes publication/lag scenarios immediately observable by fresh
clients. It does cache immutable objects. Production proxy policies may cache
metadata for its advertised TTL; testing stale proxy metadata requires changing
this fixture policy explicitly.

## Test organization

Keep existing unit/HTTP-fixture tests as the fast suite. Add a live integration
job that consumes `endpoints.json` and creates scenarios with the CLI. Public
deployment checks remain useful as a separate, optional suite.

For a status-page generator, run it into a temporary output directory after each
transition and assert its JSON status, metrics, and rendered HTML. Reuse the
output directory only when intentionally testing history and recovery. Configure
rules for one S0 and two S1s, and account for any time-based alert thresholds.

The testbed models the S3 server as a replica. Consumers that distinguish an S3
sync server can assign that role in their own configuration. The testbed does
not depend on a particular scraper, status schema, or monitoring project.
