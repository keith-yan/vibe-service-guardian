import unittest
from unittest.mock import patch

from vsg.containers import _model_runtime, _parse_wsl_line, scan_docker


class ContainerTests(unittest.TestCase):
    def test_container_and_wsl_model_runtime_signatures(self):
        self.assertEqual(_model_runtime("ghcr.io/ollama/ollama:latest"), "Ollama")
        self.assertEqual(_model_runtime("comfyui worker"), "ComfyUI")
        self.assertIsNone(_model_runtime("postgres:17"))
    def test_wsl_ss_tcp_line(self):
        service = _parse_wsl_line(
            "Ubuntu",
            'tcp LISTEN 0 4096 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=123,fd=21))',
        )
        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.process.pid, 123)
        self.assertEqual(service.process.name, "node")
        self.assertEqual(service.endpoints[0].port, 3000)
        self.assertEqual(service.endpoints[0].exposure, "loopback")
        contract = service.metadata["attribution_contract"]
        self.assertEqual(contract["ownership_namespace"], "wsl")
        self.assertEqual(contract["process_identity"]["linux_pid"], 123)
        self.assertIsNone(contract["process_identity"]["windows_host_pid"])
        self.assertEqual(contract["port_identity"]["windows_forwarding"], "not_verified")

    def test_wsl_netstat_fallback_line(self):
        service = _parse_wsl_line(
            "Debian",
            "tcp6 0 0 :::5173 :::* LISTEN 456/node",
        )
        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.process.pid, 456)
        self.assertEqual(service.process.name, "node")
        self.assertEqual(service.endpoints[0].address, "::")
        self.assertEqual(service.endpoints[0].exposure, "all_interfaces")

    def test_wsl_udp_line_is_bound(self):
        service = _parse_wsl_line(
            "Ubuntu",
            'udp UNCONN 0 0 [::1]:5353 [::]:* users:(("python",pid=789,fd=7))',
        )
        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.endpoints[0].protocol, "UDP")
        self.assertEqual(service.endpoints[0].state, "BOUND")

    def test_wsl_unknown_pid_is_not_misreported_as_windows_host_pid(self):
        service = _parse_wsl_line(
            "Ubuntu-24.04",
            "tcp LISTEN 0 4096 [::1]:8080 [::]:*",
        )
        self.assertIsNotNone(service)
        assert service is not None
        self.assertEqual(service.process.pid, 0)
        contract = service.metadata["attribution_contract"]
        self.assertIsNone(contract["process_identity"]["linux_pid"])
        self.assertIsNone(contract["process_identity"]["windows_host_pid"])
        self.assertEqual(contract["port_identity"]["model"], "guest_listener")

    def test_docker_compose_labels_create_bounded_project_contract(self):
        container_id = "a" * 64
        ps_row = (
            '{"ID":"' + container_id + '","Image":"ghcr.io/ollama/ollama:latest",'
            '"Names":"model-api","Ports":"127.0.0.1:11434->11434/tcp",'
            '"State":"running","Status":"Up 2 hours"}\n'
        )
        inspect_output = (
            '[{"Id":"'
            + container_id
            + '","Config":{"Labels":{'
            '"com.docker.compose.project":"local_ai",'
            '"com.docker.compose.service":"ollama",'
            '"com.docker.compose.project.working_dir":"E:\\\\vibe coding\\\\model-stack",'
            '"com.docker.compose.project.config_files":"E:\\\\vibe coding\\\\model-stack\\\\compose.yml"}},'
            '"HostConfig":{"RestartPolicy":{"Name":"unless-stopped"}},'
            '"State":{"Pid":9876,"Restarting":false},"RestartCount":2}]'
        )
        with (
            patch("vsg.containers.shutil.which", return_value="docker"),
            patch(
                "vsg.containers._run",
                side_effect=[
                    (0, ps_row, ""),
                    (0, inspect_output, ""),
                ],
            ),
        ):
            services, status = scan_docker()

        self.assertEqual(status["status"], "ok")
        self.assertEqual(len(services), 1)
        service = services[0]
        self.assertEqual(service.project.name, "local_ai")
        self.assertEqual(service.project.path, r"E:\vibe coding\model-stack")
        self.assertEqual(service.project.confidence, 90)
        self.assertIn("compose", service.tags)
        self.assertEqual(service.metadata["compose_service"], "ollama")
        self.assertEqual(service.metadata["compose_config_files"], ["compose.yml"])
        self.assertTrue(service.metadata["auto_restart"])
        contract = service.metadata["attribution_contract"]
        self.assertEqual(contract["ownership_namespace"], "docker")
        self.assertEqual(contract["process_identity"]["container_id"], container_id)
        self.assertEqual(contract["process_identity"]["engine_reported_pid"], 9876)
        self.assertIsNone(contract["process_identity"]["host_pid"])
        self.assertEqual(contract["port_identity"]["model"], "published_host_port")
        self.assertEqual(contract["lifecycle"]["manager"], "Docker Compose")

    def test_untrusted_compose_labels_do_not_become_project_identity(self):
        container_id = "b" * 64
        ps_row = (
            '{"ID":"' + container_id + '","Image":"postgres:17",'
            '"Names":"db","Ports":"0.0.0.0:5432->5432/tcp",'
            '"State":"running","Status":"Up"}\n'
        )
        inspect_output = (
            '[{"Id":"'
            + container_id
            + '","Config":{"Labels":{'
            '"com.docker.compose.project":"bad\\nname",'
            '"com.docker.compose.project.working_dir":"..\\\\relative"}},'
            '"HostConfig":{"RestartPolicy":{"Name":"no"}},"State":{}}]'
        )
        with (
            patch("vsg.containers.shutil.which", return_value="docker"),
            patch(
                "vsg.containers._run",
                side_effect=[(0, ps_row, ""), (0, inspect_output, "")],
            ),
        ):
            services, _status = scan_docker()
        service = services[0]
        self.assertIsNone(service.project.name)
        self.assertIsNone(service.project.path)
        self.assertEqual(
            service.metadata["attribution_contract"]["project_identity"]["source"],
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
