#!/usr/bin/env python3
"""Exercise a running testbed. Leaves it healthy; publications are retained."""

import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "software.testbed.test"
DEV = "dev.testbed.test"


def cli(*args):
    return subprocess.check_output(
        [sys.executable, str(ROOT / "testbed.py"), *args], text=True
    )


def status():
    return json.loads(cli("status"))


def revision(report, server, repo=REPO):
    return report["servers"][server]["repositories"][repo]["revision"]


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    endpoints = json.loads(cli("endpoints"))
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    before = status()
    check(before["synced"], "Initial deployment is not synchronized")
    inspection = json.loads(cli("inspect"))
    for service in ("s0", "s1", "s1-s3", "s1-s3-worker", "object-store", "squid"):
        info = inspection["services"][service]
        check(info["running"], f"{service} is not running")
        check(
            info["networks"][endpoints["network"]]["ipv4"],
            f"No active IPv4 address for {service}",
        )
    check(
        inspection["networks"][endpoints["network"]]["ipam"],
        "Network subnet information is missing",
    )
    for server in ("s0", "s1"):
        with direct.open(
            endpoints["host"][server] + "/cvmfs/info/v1/repositories.json", timeout=10
        ) as response:
            index = json.load(response)
        entries = index["repositories" if server == "s0" else "replicas"]
        check(
            {entry["name"] for entry in entries} == {REPO, DEV},
            "Discovery returned unexpected repositories",
        )
    try:
        direct.open(
            endpoints["host"]["s1-s3"] + "/cvmfs/info/v1/repositories.json", timeout=10
        )
    except urllib.error.HTTPError as error:
        check(error.code == 404, "S3 index must return 404")
    else:
        raise AssertionError("S3 unexpectedly exposes an index")
    print("PASS: discovery and initial replication", flush=True)

    # Publish custom content, proving the source import path as well as revision changes.
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "custom.txt").write_text("custom integration content\n")
        cli("publish", REPO, "--source", directory, "--message", "integration revision")
    after = status()
    check(
        revision(after, "s0") > revision(before, "s0"), "Publishing did not advance S0"
    )
    check(
        revision(after, "s1") == revision(before, "s1"),
        "Normal S1 replicated automatically",
    )
    check(
        revision(after, "s1-s3") == revision(before, "s1-s3"),
        "S3 S1 replicated automatically",
    )
    cli("replicate", "s1", REPO)
    lag = status()
    check(revision(lag, "s1") == revision(lag, "s0"), "Normal S1 did not catch up")
    check(revision(lag, "s1-s3") < revision(lag, "s0"), "S3 S1 should remain behind")
    check(
        revision(lag, "s0", DEV) == revision(before, "s0", DEV),
        "Publishing changed the other repository",
    )
    cli("replicate", "s1-s3", REPO)
    cli("wait", "--synced")
    print("PASS: custom content, publication, selective lag and recovery", flush=True)

    # Cache the immutable catalog through Squid, then read it a second time.
    report = status()
    catalog = report["servers"]["s1"]["repositories"][REPO]["catalog_hash"]
    object_path = f"/cvmfs/{REPO}/data/{catalog[:2]}/{catalog[2:]}C"
    proxy = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": endpoints["host"]["squid"]})
    )
    object_url = endpoints["internal"]["s1"] + object_path
    with proxy.open(object_url, timeout=15) as response:
        first = response.read()
    with proxy.open(object_url, timeout=15) as response:
        second = response.read()
    check(first == second and first, "Cache returned different or empty content")
    access = cli("exec", "squid", "--", "cat", "/var/log/squid/access.log")
    hits = [
        line
        for line in access.splitlines()
        if object_path in line and ("TCP_HIT" in line or "TCP_MEM_HIT" in line)
    ]
    check(hits, "Repeated immutable-object request did not produce a Squid hit")
    check(
        cli("client-read", REPO, "custom.txt") == "custom integration content\n",
        "FUSE client returned incorrect content",
    )
    print("PASS: Squid cache hit and signed FUSE client read", flush=True)

    for service, affected in (
        ("s0", "s0"),
        ("s1", "s1"),
        ("s1-s3", "s1-s3"),
        ("object-store", "s1-s3"),
    ):
        try:
            cli("stop", service)
            info = json.loads(cli("inspect"))["services"][service]
            check(not info["running"], f"Inspection should report {service} stopped")
            report = status()
            check(
                not report["servers"][affected]["reachable"],
                f"{service} outage was not observed",
            )
            check(
                any(
                    value["reachable"]
                    for name, value in report["servers"].items()
                    if name != affected
                ),
                "Outage affected every server",
            )
            if service == "s0":
                check(
                    cli("client-read", REPO, "custom.txt")
                    == "custom integration content\n",
                    "S0 outage prevented replica reads",
                )
        finally:
            cli("start", service)
        cli("wait", "--synced", "--timeout", "90")
        check(
            json.loads(cli("endpoints")) == endpoints,
            "Endpoint URLs changed across restart",
        )
        print(f"PASS: {service} outage and recovery", flush=True)

    # Prove that recovering the publisher restores its writable mounts too.
    cli("publish", DEV, "--message", "publication after S0 restart")
    cli("replicate", "s1-s3", DEV)
    report = status()
    check(
        revision(report, "s1", DEV) < revision(report, "s1-s3", DEV),
        "Normal S1 should remain behind",
    )
    cli("replicate", "s1", DEV)
    cli("wait", "--synced")
    print("PASS: publish after restart and normal S1 lag", flush=True)

    try:
        cli("pause", "s1")
        check(
            json.loads(cli("inspect"))["services"]["s1"]["paused"],
            "Inspection should report S1 paused",
        )
        check(not status()["servers"]["s1"]["reachable"], "Paused S1 should time out")
    finally:
        cli("resume", "s1")
    cli("wait", "--synced")
    print("PASS: frozen S1 timeout and resume", flush=True)

    try:
        cli("stop", "squid")
        try:
            proxy.open(object_url, timeout=3)
        except OSError:
            pass
        else:
            raise AssertionError("Request through stopped Squid unexpectedly succeeded")
        check(
            status()["synced"], "Squid outage should not affect direct server scrapes"
        )
    finally:
        cli("start", "squid")
    check(
        cli("client-read", REPO, "custom.txt") == "custom integration content\n",
        "Squid failed to recover",
    )
    print("PASS: Squid outage and recovery", flush=True)


if __name__ == "__main__":
    main()
