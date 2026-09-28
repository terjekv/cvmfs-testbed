#!/usr/bin/env python3
"""A local and CI CernVM-FS deployment controller; Python standard library only."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPOSITORIES = ("software.testbed.test", "dev.testbed.test")
SERVERS = ("s0", "s1", "s1-s3")
SERVICES = (*SERVERS, "object-store", "squid")
DEFAULT_VERSION = "2.14.1"
DEFAULT_PLATFORM = "auto"


class TestbedError(Exception):
    pass


def run(command, *, capture=False, timeout=300, merge_stderr=False):
    try:
        result = subprocess.run(
            [str(arg) for arg in command],
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else sys.stderr,
            stderr=subprocess.STDOUT if merge_stderr else None,
            timeout=timeout,
        )
        return result.stdout if capture else ""
    except FileNotFoundError as error:
        raise TestbedError(f"Required executable not found: {command[0]}") from error
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise TestbedError(
            f"Command failed: {' '.join(map(str, command))}: {error}"
        ) from error


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise TestbedError(f"Cannot read {path}: {error}") from error


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def runtime_command(runtime, sudo=False):
    executable = shutil.which(runtime)
    if not executable:
        raise TestbedError(f"Required executable not found: {runtime}")
    return (["sudo", "-n"] if sudo else []) + [executable]


def select_runtime(requested, sudo=False):
    failures = []
    for runtime in ("docker", "podman") if requested == "auto" else (requested,):
        try:
            command = runtime_command(runtime, sudo)
            info = json.loads(
                run(command + ["info", "--format", "{{json .}}"], capture=True)
            )
            host = info.get("host", {}) if runtime == "podman" else info
            if host.get("os", host.get("OSType")) != "linux":
                raise TestbedError("The container engine must run Linux containers")
            if runtime == "podman":
                if host.get("security", {}).get("rootless"):
                    raise TestbedError(
                        "Podman requires rootful containers for FUSE/OverlayFS; use up --runtime podman --sudo"
                    )
                run(
                    runtime_command("podman-compose", sudo) + ["--version"],
                    capture=True,
                )
            else:
                run(command + ["compose", "version"], capture=True)
            return runtime, host.get("arch", host.get("Architecture"))
        except (TestbedError, ValueError) as error:
            failures.append(f"{runtime}: {error}")
    raise TestbedError("No usable container runtime: " + "; ".join(failures))


def network_info(network):
    if "name" in network:  # Podman uses the netavark network schema.
        return network["name"], {
            "id": network["id"],
            "driver": network["driver"],
            "ipam": [
                {"Subnet": subnet["subnet"], "Gateway": subnet.get("gateway")}
                for subnet in network.get("subnets", [])
            ],
        }
    return network["Name"], {
        "id": network["Id"],
        "driver": network["Driver"],
        "ipam": network["IPAM"].get("Config") or [],
    }


def parse_manifest(data, expected_name):
    """Read the text portion without trying to decode the binary signature."""
    fields = {}
    for line in data.split(b"\n"):
        if line == b"--":
            break
        if line[:1] in (b"N", b"S", b"C"):
            fields[line[:1].decode()] = line[1:].decode("ascii")
    if fields.get("N") != expected_name:
        raise TestbedError(f"Manifest name mismatch: expected {expected_name}")
    if not re.fullmatch(r"[0-9]+", fields.get("S", "")) or not fields.get("C"):
        raise TestbedError("Manifest lacks a valid revision or catalog hash")
    return {"revision": int(fields["S"]), "catalog_hash": fields["C"]}


def fetch(url, timeout=3):
    # Host-side probes must bypass the user's ambient HTTP proxy configuration.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as response:
        return response.read(2 * 1024 * 1024)


def probe(endpoints):
    result = {"repositories": list(REPOSITORIES), "servers": {}}
    for server in SERVERS:
        origin = endpoints["host"][server]
        entry = {"url": origin, "reachable": False, "repositories": {}}
        try:
            for repo in REPOSITORIES:
                base = f"{origin}/cvmfs/{repo}"
                manifest = parse_manifest(fetch(base + "/.cvmfspublished"), repo)
                metadata = json.loads(fetch(base + "/.cvmfs_status.json"))
                if not isinstance(metadata, dict):
                    raise TestbedError("Repository status must be a JSON object")
                entry["repositories"][repo] = {**manifest, "status": metadata}
            entry["reachable"] = True
        except (OSError, ValueError, TestbedError) as error:
            entry["error"] = str(error)
        result["servers"][server] = entry
    result["healthy"] = all(item["reachable"] for item in result["servers"].values())
    result["synced"] = result["healthy"] and all(
        len(
            {
                result["servers"][server]["repositories"][repo]["catalog_hash"]
                for server in SERVERS
            }
        )
        == 1
        and len(
            {
                result["servers"][server]["repositories"][repo]["revision"]
                for server in SERVERS
            }
        )
        == 1
        for repo in REPOSITORIES
    )
    return result


def container_info(container):
    """Expose operational/network fields without leaking container environment data."""
    state = container["State"]
    networks = {}
    for name, network in (
        container.get("NetworkSettings", {}).get("Networks", {}).items()
    ):
        networks[name] = {
            "ipv4": network.get("IPAddress") or None,
            "ipv6": network.get("GlobalIPv6Address") or None,
            "gateway": network.get("Gateway") or None,
            "ipv6_gateway": network.get("IPv6Gateway") or None,
            "aliases": network.get("Aliases") or [],
        }
    ports = []
    for port, bindings in (
        container.get("NetworkSettings", {}).get("Ports") or {}
    ).items():
        for binding in bindings or []:
            ports.append(
                {
                    "container_port": port,
                    "host_ip": binding["HostIp"],
                    "host_port": int(binding["HostPort"]),
                }
            )
    return {
        "container_id": container["Id"],
        "container_name": container["Name"].lstrip("/"),
        "hostname": container["Config"]["Hostname"],
        "state": state["Status"],
        "running": state["Running"],
        "paused": state.get("Paused", False),
        "health": (state.get("Health") or state.get("Healthcheck") or {}).get("Status")
        or None,
        "exit_code": state.get("ExitCode"),
        "networks": networks,
        "published_ports": ports,
    }


class Testbed:
    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        self.state_path = self.directory / "state.json"
        self.endpoint_path = self.directory / "endpoints.json"

    def state(self):
        state = read_json(self.state_path)
        if state.get("schema") != 1 or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,47}", state.get("project", "")
        ):
            raise TestbedError("Invalid testbed state")
        if state.get("runtime", "docker") not in ("docker", "podman") or not isinstance(
            state.get("sudo", False), bool
        ):
            raise TestbedError("Invalid runtime in testbed state")
        return state

    def engine(self, *arguments, capture=False, timeout=300):
        state = self.state()
        return run(
            runtime_command(state.get("runtime", "docker"), state.get("sudo", False))
            + list(arguments),
            capture=capture,
            timeout=timeout,
        )

    def compose(self, *arguments, capture=False, timeout=300):
        state = self.state()
        if state.get("runtime", "docker") == "podman":
            command = runtime_command("podman-compose", state.get("sudo", False)) + [
                "--in-pod",
                "false",
            ]
        else:
            command = runtime_command("docker", state.get("sudo", False)) + ["compose"]
        # Explicit --env-file and absolute --file make commands independent of cwd.
        return run(
            command
            + [
                "--project-name",
                state["project"],
                "--env-file",
                self.directory / "runtime.env",
                "--file",
                ROOT / "compose.yml",
                *arguments,
            ],
            capture=capture,
            timeout=timeout,
        )

    def execute(self, service, *arguments, capture=False):
        return self.engine(
            "exec", self.container_id(service), *arguments, capture=capture
        )

    def containers(self):
        ids = self.engine(
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={self.state()['project']}",
            capture=True,
        ).split()
        return json.loads(self.engine("inspect", *ids, capture=True)) if ids else []

    def container_id(self, service):
        matches = [
            container["Id"]
            for container in self.containers()
            if container["Config"]["Labels"].get("com.docker.compose.service")
            == service
        ]
        if len(matches) != 1:
            raise TestbedError(
                f"Expected one container for {service}, found {len(matches)}"
            )
        return matches[0]

    def copy(self, source, destination):
        def resolve(value):
            service, separator, path = value.partition(":")
            return (
                self.container_id(service) + separator + path
                if separator and service in (*SERVICES, "s1-s3-worker")
                else value
            )

        resolved_source, resolved_destination = resolve(source), resolve(destination)
        self.engine("cp", resolved_source, resolved_destination)
        if self.state().get("sudo", False) and resolved_destination == destination:
            # Rootful Podman preserves container file ownership on export. Give
            # the calling user access to just this exported file/directory tree.
            run(
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-hR",
                    "--",
                    f"{os.getuid()}:{os.getgid()}",
                    destination,
                ]
            )

    def inspect(self):
        state = self.state()
        containers = self.containers()
        network_name = state["project"] + "_network"
        network_ids = self.engine(
            "network",
            "ls",
            "--quiet",
            "--filter",
            f"label=com.docker.compose.project={state['project']}",
            capture=True,
        ).split()
        networks = (
            json.loads(self.engine("network", "inspect", *network_ids, capture=True))
            if network_ids
            else []
        )
        return {
            "schema": 1,
            "project": state["project"],
            "runtime": state.get("runtime", "docker"),
            "services": {
                container["Config"]["Labels"][
                    "com.docker.compose.service"
                ]: container_info(container)
                for container in containers
                if container["Config"]["Labels"]
                .get("com.docker.compose.oneoff", "false")
                .lower()
                != "true"
            },
            "networks": {
                name: info
                for name, info in map(network_info, networks)
                if name == network_name
            },
        }

    def repository(self, service, operation, repo, *arguments):
        self.execute(service, "/opt/testbed/repository.sh", operation, repo, *arguments)

    def up(self, args):
        if self.state_path.exists():
            raise TestbedError(
                "This state directory already owns a deployment; use status/start or down before up"
            )
        runtime, architecture = select_runtime(args.runtime, args.sudo)
        platform = args.platform
        if platform == "auto":
            arch = {
                "aarch64": "arm64",
                "arm64": "arm64",
                "x86_64": "amd64",
                "amd64": "amd64",
            }.get(architecture)
            if not arch:
                raise TestbedError(
                    f"Unsupported container architecture: {architecture}"
                )
            platform = "linux/" + arch
        project = (
            args.project_name
            or "cvmfs-testbed-"
            + hashlib.sha256(str(self.directory).encode()).hexdigest()[:10]
        )
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", project):
            raise TestbedError("Project name must match [a-z0-9][a-z0-9_-]{0,47}")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.cvmfs_version):
            raise TestbedError("CernVM-FS version must have the form X.Y.Z")
        existing = run(
            runtime_command(runtime, args.sudo)
            + [
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            capture=True,
        )
        if existing.strip():
            raise TestbedError(
                f"Compose project {project} already exists; choose another project name"
            )
        state = {
            "schema": 1,
            "project": project,
            "runtime": runtime,
            "sudo": args.sudo,
            "platform": platform,
            "cvmfs_version": args.cvmfs_version,
        }
        write_json(self.state_path, state)
        (self.directory / "runtime.env").write_text(
            f"TESTBED_PROJECT={project}\nTESTBED_PLATFORM={platform}\n"
            f"CVMFS_VERSION={args.cvmfs_version}\nTESTBED_IMAGE=localhost/cvmfs-testbed-server:{args.cvmfs_version}-{platform.split('/')[1]}\n"
        )
        try:
            # Build/pull before assigning host ports to minimize the reservation
            # window. Explicit port bindings survive container stop/start cycles.
            self.compose("build", "s0", timeout=1200)
            self.compose("pull", "object-store", "s1-s3", timeout=300)
            with contextlib.ExitStack() as stack:
                ports = {}
                for name in ("S0_PORT", "S1_PORT", "S3_PORT", "SQUID_PORT"):
                    sock = stack.enter_context(socket.socket())
                    sock.bind(("127.0.0.1", 0))
                    ports[name] = sock.getsockname()[1]
                with (self.directory / "runtime.env").open("a") as configuration:
                    for name, port in ports.items():
                        configuration.write(f"{name}={port}\n")
            self.compose(
                "up",
                "--detach",
                "--no-build",
                timeout=300,
            )
            self.wait_services((*SERVICES, "s1-s3-worker"))
            self.write_endpoints()
            self.wait_http("s0")
            self.wait_http("s1")
            self.execute("s1-s3-worker", "python3", "/opt/testbed/init-s3.py")
            for repo in REPOSITORIES:
                self.repository("s0", "create", repo)
            # The scheduled-check entry point initializes S0 monitoring status.
            # Individual checks only update a status file that already exists.
            self.execute("s0", "cvmfs_server", "check", "-a")
            for service in ("s1", "s1-s3-worker"):
                for repo in REPOSITORIES:
                    self.repository(service, "add-replica", repo)
                    self.repository(service, "replicate", repo)
            keys = self.directory / "keys"
            keys.mkdir(exist_ok=True)
            for repo in REPOSITORIES:
                self.copy(f"s0:/public-keys/{repo}.pub", str(keys / f"{repo}.pub"))
            self.wait(synced=True, timeout=90)
            endpoints = read_json(self.endpoint_path)
            proxy = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": endpoints["host"]["squid"]})
            )
            url = (
                endpoints["internal"]["s1"]
                + f"/cvmfs/{REPOSITORIES[0]}/.cvmfspublished"
            )
            with proxy.open(url, timeout=10) as response:
                parse_manifest(response.read(), REPOSITORIES[0])
        except Exception:
            print(
                f"Setup failed; deployment retained for diagnosis. Run cvmfs-testbed --state-dir {self.directory} logs/down.",
                file=sys.stderr,
            )
            raise
        print(json.dumps(read_json(self.endpoint_path), indent=2))

    def write_endpoints(self):
        state = self.state()
        host = {}
        for service in (*SERVERS, "squid"):
            port = "3128" if service == "squid" else "80"
            address = (
                self.engine("port", self.container_id(service), port, capture=True)
                .strip()
                .splitlines()[0]
            )
            host[service] = "http://" + address
        internal = {server: f"http://{server}.testbed.test" for server in SERVERS}
        internal["squid"] = "http://squid.testbed.test:3128"
        write_json(
            self.endpoint_path,
            {
                "schema": 1,
                "project": state["project"],
                "runtime": state.get("runtime", "docker"),
                "network": state["project"] + "_network",
                "repositories": list(REPOSITORIES),
                "host": host,
                "internal": internal,
                "keys_directory": str(self.directory / "keys"),
            },
        )

    def wait_http(self, service, timeout=60):
        origin = read_json(self.endpoint_path)["host"][service]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                fetch(origin)
                return
            except urllib.error.HTTPError:
                return  # A responding HTTP server is ready for repository setup.
            except OSError:
                time.sleep(0.5)
        raise TestbedError(f"Timed out waiting for {service} HTTP")

    def wait_services(self, services, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = {
                c["Config"]["Labels"].get("com.docker.compose.service"): container_info(
                    c
                )
                for c in self.containers()
            }
            if all(
                service in current
                and current[service]["running"]
                and not current[service]["paused"]
                and current[service]["health"] in (None, "healthy")
                for service in services
            ):
                return
            time.sleep(0.5)
        summary = {
            service: {
                key: current.get(service, {}).get(key)
                for key in ("state", "running", "paused", "health", "exit_code")
            }
            for service in services
        }
        raise TestbedError(f"Timed out waiting for services: {json.dumps(summary)}")

    def wait(self, *, synced=False, timeout=60):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = probe(read_json(self.endpoint_path))
            if last["synced" if synced else "healthy"]:
                return last
            time.sleep(0.5)
        raise TestbedError(f"Readiness deadline exceeded: {json.dumps(last)}")

    def control(self, operation, service):
        targets = ["s1-s3", "s1-s3-worker"] if service == "s1-s3" else [service]
        verb = {"resume": "unpause"}.get(operation, operation)
        self.engine(verb, *(self.container_id(target) for target in targets))
        if operation == "start":
            self.wait_services(targets)
            if service in SERVERS:
                self.wait_http(service)

    def publish(self, args):
        source = None
        if args.source:
            source = Path(args.source).resolve()
            if not source.is_dir():
                raise TestbedError("--source must be a directory")
        destination = "/tmp/testbed-import-" + uuid.uuid4().hex if source else ""
        try:
            if source:
                self.copy(str(source) + "/.", "s0:" + destination)
            self.repository("s0", "publish", args.repository, args.message, destination)
        finally:
            if source:
                self.execute("s0", "rm", "-rf", destination)

    def replicate(self, args):
        services = (
            ("s1", "s1-s3-worker")
            if args.replica == "all"
            else ("s1-s3-worker" if args.replica == "s1-s3" else "s1",)
        )
        for service in services:
            for repo in (args.repository,) if args.repository else REPOSITORIES:
                self.repository(service, "replicate", repo)

    def client_read(self, repository, path):
        state = self.state()
        image = f"localhost/cvmfs-testbed-server:{state['cvmfs_version']}-{state['platform'].split('/')[1]}"
        s0 = next(
            c
            for c in self.containers()
            if c["Config"]["Labels"].get("com.docker.compose.service") == "s0"
        )
        keys = next(
            mount["Name"]
            for mount in s0["Mounts"]
            if mount["Destination"] == "/public-keys"
        )
        return self.engine(
            "run",
            "--rm",
            "--init",
            "--privileged",
            "--network",
            state["project"] + "_network",
            "--volume",
            keys + ":/public-keys:ro",
            "--entrypoint",
            "/opt/testbed/client-read.sh",
            image,
            repository,
            path,
            capture=True,
        )

    def logs(self, output):
        directory = Path(output).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        # Both engines write parts of their logs to stderr, so capture both streams.
        with (directory / "compose.log").open("w") as log:
            for container in self.containers():
                log.write(f"\n=== {container['Name']} ===\n")
                log.flush()
                state = self.state()
                log.write(
                    run(
                        runtime_command(
                            state.get("runtime", "docker"), state.get("sudo", False)
                        )
                        + ["logs", container["Id"]],
                        capture=True,
                        merge_stderr=True,
                        timeout=60,
                    )
                )
        write_json(directory / "inspect.json", self.inspect())
        for filename in ("state.json", "endpoints.json"):
            path = self.directory / filename
            if path.exists():
                (directory / filename).write_bytes(path.read_bytes())
        # Container-local logs are useful even if a service was deliberately stopped.
        for service, path in (
            ("s0", "/var/log/cvmfs"),
            ("s1", "/var/log/cvmfs"),
            ("s1-s3-worker", "/var/log/cvmfs"),
            ("squid", "/var/log/squid"),
        ):
            target = directory / service
            target.mkdir(exist_ok=True)
            try:
                self.copy(f"{service}:{path}/.", str(target))
            except TestbedError as error:
                print(f"Optional log collection: {error}", file=sys.stderr)
        print(directory)

    def down(self):
        if not self.state_path.exists():
            return
        for container in self.containers():
            if container["State"].get("Paused"):
                self.engine("unpause", container["Id"])
        self.compose("down", "--volumes", "--remove-orphans")
        if self.containers():
            raise TestbedError(
                "Containers remain after teardown; state retained for retry"
            )
        for name in ("state.json", "runtime.env", "endpoints.json"):
            (self.directory / name).unlink(missing_ok=True)
        for repo in REPOSITORIES:
            (self.directory / "keys" / f"{repo}.pub").unlink(missing_ok=True)


@contextlib.contextmanager
def locked(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TestbedError(
                "Another command is changing this deployment; try again after it finishes"
            ) from error
        yield


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--state-dir", default=os.environ.get("CVMFS_TESTBED_STATE", ".cvmfs-testbed")
    )
    commands = root.add_subparsers(dest="command", required=True)
    up = commands.add_parser(
        "up", help="Build, start, seed and synchronize a fresh testbed"
    )
    up.add_argument("--project-name")
    up.add_argument(
        "--runtime",
        choices=("auto", "docker", "podman"),
        default="auto",
        help="Prefer Docker, then Podman, or select explicitly",
    )
    up.add_argument(
        "--sudo",
        action="store_true",
        help="Run the container engine and Compose provider through sudo -n",
    )
    up.add_argument(
        "--platform",
        choices=("auto", "linux/amd64", "linux/arm64"),
        default=DEFAULT_PLATFORM,
    )
    up.add_argument("--cvmfs-version", default=DEFAULT_VERSION)
    commands.add_parser(
        "down", help="Remove this deployment, including its data volumes"
    )
    commands.add_parser("endpoints", help="Print host and container endpoint JSON")
    inspect = commands.add_parser(
        "inspect",
        help="Query live container IPs, aliases, ports, networks and process state",
    )
    inspect.add_argument("--json", action="store_true", help="Output is always JSON")
    status = commands.add_parser(
        "status", help="Report reachability and repository revisions"
    )
    status.add_argument(
        "--json",
        action="store_true",
        help="Accepted for clarity; output is always JSON",
    )
    for operation in ("stop", "start", "pause", "resume"):
        command = commands.add_parser(operation)
        command.add_argument("service", choices=SERVICES)
    publish = commands.add_parser(
        "publish", help="Publish a revision on S0; replicas remain unchanged"
    )
    publish.add_argument("repository", choices=REPOSITORIES)
    publish.add_argument("--message", default="Testbed publication")
    publish.add_argument("--source", help="Directory to merge into the repository")
    replicate = commands.add_parser(
        "replicate", help="Explicitly snapshot one or both replicas"
    )
    replicate.add_argument("replica", choices=("s1", "s1-s3", "all"))
    replicate.add_argument("repository", choices=REPOSITORIES, nargs="?")
    wait = commands.add_parser("wait", help="Wait for all repositories to be readable")
    wait.add_argument("--synced", action="store_true")
    wait.add_argument("--timeout", type=float, default=60)
    logs = commands.add_parser(
        "logs", help="Collect service logs, endpoints and container state"
    )
    logs.add_argument("--output", default="artifacts/testbed")
    client = commands.add_parser(
        "client-read", help="Read a file through a real FUSE client and Squid"
    )
    client.add_argument("repository", choices=REPOSITORIES)
    client.add_argument("path", nargs="?", default="revision.txt")
    execute = commands.add_parser(
        "exec", help="Run a diagnostic command inside a service"
    )
    execute.add_argument("service", choices=(*SERVICES, "s1-s3-worker"))
    execute.add_argument("arguments", nargs=argparse.REMAINDER)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    bed = Testbed(args.state_dir)
    try:
        if args.command == "status":
            print(json.dumps(probe(read_json(bed.endpoint_path)), indent=2))
        elif args.command == "endpoints":
            print(json.dumps(read_json(bed.endpoint_path), indent=2))
        elif args.command == "inspect":
            print(json.dumps(bed.inspect(), indent=2))
        else:
            with locked(bed.directory):
                if args.command == "up":
                    bed.up(args)
                elif args.command == "down":
                    bed.down()
                elif args.command in ("stop", "start", "pause", "resume"):
                    bed.control(args.command, args.service)
                elif args.command == "publish":
                    bed.publish(args)
                elif args.command == "replicate":
                    bed.replicate(args)
                elif args.command == "wait":
                    if args.timeout <= 0:
                        raise TestbedError("--timeout must be positive")
                    print(
                        json.dumps(
                            bed.wait(synced=args.synced, timeout=args.timeout), indent=2
                        )
                    )
                elif args.command == "logs":
                    bed.logs(args.output)
                elif args.command == "client-read":
                    if args.path.startswith("/") or ".." in Path(args.path).parts:
                        raise TestbedError(
                            "Client path must be relative and cannot contain '..'"
                        )
                    print(
                        bed.client_read(args.repository, args.path),
                        end="",
                    )
                elif args.command == "exec":
                    arguments = (
                        args.arguments[1:]
                        if args.arguments[:1] == ["--"]
                        else args.arguments
                    )
                    if not arguments:
                        raise TestbedError(
                            "exec requires a command after the service name"
                        )
                    print(bed.execute(args.service, *arguments, capture=True), end="")
        return 0
    except (TestbedError, OSError) as error:
        print(f"cvmfs-testbed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
