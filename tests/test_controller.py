import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch
from urllib.error import URLError

spec = importlib.util.spec_from_file_location(
    "testbed", Path(__file__).resolve().parents[1] / "testbed.py"
)
testbed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(testbed)


def manifest(repo, revision=1, catalog="a" * 40):
    return (
        f"C{catalog}\nN{repo}\nS{revision}\n--\n".encode() + b"\xff\x00binary signature"
    )


class ManifestTests(unittest.TestCase):
    def test_binary_signature_does_not_require_utf8(self):
        self.assertEqual(
            testbed.parse_manifest(
                manifest(testbed.REPOSITORIES[0], 12), testbed.REPOSITORIES[0]
            )["revision"],
            12,
        )

    def test_rejects_other_repository(self):
        with self.assertRaisesRegex(testbed.TestbedError, "name mismatch"):
            testbed.parse_manifest(manifest("other.test"), testbed.REPOSITORIES[0])

    def test_rejects_missing_revision(self):
        with self.assertRaises(testbed.TestbedError):
            testbed.parse_manifest(
                b"Nsoftware.testbed.test\nCabc\n--\n", testbed.REPOSITORIES[0]
            )


class StatusTests(unittest.TestCase):
    endpoints: ClassVar[dict] = {
        "host": {server: f"http://{server}" for server in testbed.SERVERS}
    }

    def responses(self, url):
        repo = url.split("/")[-2]
        return b"{}" if url.endswith(".json") else manifest(repo)

    def test_synchronized(self):
        with patch.object(testbed, "fetch", side_effect=self.responses):
            report = testbed.probe(self.endpoints)
        self.assertTrue(report["healthy"])
        self.assertTrue(report["synced"])

    def test_outage_preserves_other_server_results(self):
        def responses(url):
            if url.startswith("http://s0/"):
                raise URLError("connection refused")
            return self.responses(url)

        with patch.object(testbed, "fetch", side_effect=responses):
            report = testbed.probe(self.endpoints)
        self.assertFalse(report["healthy"])
        self.assertFalse(report["synced"])
        self.assertTrue(report["servers"]["s1"]["reachable"])
        self.assertIn("connection refused", report["servers"]["s0"]["error"])

    def test_equal_revisions_with_different_catalogs_are_not_synced(self):
        def responses(url):
            if url.startswith("http://s1/") and url.endswith(".cvmfspublished"):
                return manifest(url.split("/")[-2], catalog="b" * 40)
            return self.responses(url)

        with patch.object(testbed, "fetch", side_effect=responses):
            report = testbed.probe(self.endpoints)
        self.assertTrue(report["healthy"])
        self.assertFalse(report["synced"])

    def test_invalid_status_is_reported_as_failure(self):
        with patch.object(
            testbed,
            "fetch",
            side_effect=lambda url: (
                b"[]" if url.endswith(".json") else self.responses(url)
            ),
        ):
            self.assertFalse(testbed.probe(self.endpoints)["healthy"])


class OwnershipTests(unittest.TestCase):
    def test_down_without_state_does_not_call_docker(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(testbed, "run") as run,
        ):
            testbed.Testbed(directory).down()
            run.assert_not_called()

    def test_rejects_invalid_project_in_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "state.json").write_text(
                json.dumps({"schema": 1, "project": "../other"})
            )
            with self.assertRaises(testbed.TestbedError):
                testbed.Testbed(path).state()


class InspectionTests(unittest.TestCase):
    def test_network_schemas_have_the_same_public_shape(self):
        docker = {
            "Name": "test_network",
            "Id": "abc",
            "Driver": "bridge",
            "IPAM": {"Config": [{"Subnet": "10.89.0.0/24", "Gateway": "10.89.0.1"}]},
        }
        podman = {
            "name": "test_network",
            "id": "abc",
            "driver": "bridge",
            "subnets": [{"subnet": "10.89.0.0/24", "gateway": "10.89.0.1"}],
        }
        self.assertEqual(testbed.network_info(docker), testbed.network_info(podman))

    def test_inspection_exposes_network_state_without_environment(self):
        container = {
            "Id": "abc",
            "Name": "/sample-s0-1",
            "Config": {"Hostname": "s0.testbed.test", "Env": ["SECRET=private"]},
            "State": {
                "Status": "running",
                "Running": True,
                "Paused": True,
                "Health": {"Status": "healthy"},
                "ExitCode": 0,
            },
            "NetworkSettings": {
                "Networks": {
                    "sample_network": {
                        "IPAddress": "172.20.0.3",
                        "GlobalIPv6Address": "",
                        "Gateway": "172.20.0.1",
                        "Aliases": ["s0.testbed.test"],
                    }
                },
                "Ports": {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "12345"}]},
            },
        }
        info = testbed.container_info(container)
        self.assertEqual(info["networks"]["sample_network"]["ipv4"], "172.20.0.3")
        self.assertIsNone(info["networks"]["sample_network"]["ipv6"])
        self.assertTrue(info["paused"])
        self.assertEqual(info["published_ports"][0]["host_port"], 12345)
        self.assertNotIn("private", json.dumps(info))

    def test_stopped_container_can_have_no_addresses_or_ports(self):
        info = testbed.container_info(
            {
                "Id": "abc",
                "Name": "/sample-s0-1",
                "Config": {"Hostname": "s0.testbed.test"},
                "State": {"Status": "exited", "Running": False},
                "NetworkSettings": {"Networks": {}, "Ports": None},
            }
        )
        self.assertFalse(info["running"])
        self.assertEqual(info["published_ports"], [])
        self.assertEqual(info["networks"], {})


class RuntimeTests(unittest.TestCase):
    def test_auto_falls_back_to_podman_without_docker(self):
        def which(name):
            return None if name == "docker" else "/usr/bin/" + name

        info = {
            "host": {"os": "linux", "arch": "arm64", "security": {"rootless": False}}
        }
        with (
            patch.object(testbed.shutil, "which", side_effect=which),
            patch.object(testbed, "run", side_effect=[json.dumps(info), "1.5.0"]),
        ):
            self.assertEqual(testbed.select_runtime("auto"), ("podman", "arm64"))

    def test_explicit_runtime_does_not_fall_back(self):
        with (
            patch.object(testbed.shutil, "which", return_value=None),
            self.assertRaisesRegex(testbed.TestbedError, "not found: podman"),
        ):
            testbed.select_runtime("podman")

    def test_rootless_podman_has_actionable_error(self):
        info = {
            "host": {"os": "linux", "arch": "amd64", "security": {"rootless": True}}
        }
        with (
            patch.object(testbed.shutil, "which", return_value="/usr/bin/podman"),
            patch.object(testbed, "run", return_value=json.dumps(info)),
            self.assertRaisesRegex(testbed.TestbedError, "--runtime podman --sudo"),
        ):
            testbed.select_runtime("podman")

    def test_start_waits_only_for_selected_service(self):
        with tempfile.TemporaryDirectory() as directory:
            bed = testbed.Testbed(directory)
            with (
                patch.object(bed, "container_id", return_value="s0-id"),
                patch.object(bed, "engine") as engine,
                patch.object(bed, "wait_services") as wait,
                patch.object(bed, "wait_http"),
            ):
                bed.control("start", "s0")
            engine.assert_called_once_with("start", "s0-id")
            wait.assert_called_once_with(["s0"])


if __name__ == "__main__":
    unittest.main()
