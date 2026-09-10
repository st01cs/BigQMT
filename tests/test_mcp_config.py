import unittest

from bigqmt.mcp.config import (
    McpConfig,
    McpConfigError,
    config_from_mapping,
    is_loopback,
    load_mcp_config,
)


class McpConfigDefaultTest(unittest.TestCase):
    def test_defaults_are_local_only(self):
        cfg = McpConfig()
        self.assertEqual(cfg.host, "127.0.0.1")
        self.assertEqual(cfg.port, 9000)
        self.assertFalse(cfg.allow_remote)
        self.assertIsNone(cfg.auth_token)
        self.assertEqual(cfg.qmt_base_url, "http://127.0.0.1:10086")
        self.assertEqual(cfg.qmt_token, "123456789")
        self.assertFalse(cfg.binds_remote)

    def test_default_validate_passes(self):
        cfg = McpConfig().validate()
        self.assertIsInstance(cfg, McpConfig)
        self.assertEqual(cfg.host, "127.0.0.1")

    def test_loopback_detection(self):
        for host in ("127.0.0.1", "localhost", "::1", ""):
            self.assertTrue(is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.10"):
            self.assertFalse(is_loopback(host), host)


class McpConfigMappingTest(unittest.TestCase):
    def test_env_keys_are_parsed(self):
        cfg = config_from_mapping(
            {
                "QMT_MCP_HOST": "0.0.0.0",
                "QMT_MCP_PORT": "9100",
                "QMT_MCP_ALLOW_REMOTE": "true",
                "QMT_MCP_AUTH_TOKEN": "s3cret",
                "QMT_HTTP_BASE_URL": "http://127.0.0.1:12345",
                "QMT_HTTP_TOKEN": "abc",
                "QMT_MCP_REQUEST_TIMEOUT": "3.5",
            }
        )
        self.assertEqual(cfg.host, "0.0.0.0")
        self.assertEqual(cfg.port, 9100)
        self.assertTrue(cfg.allow_remote)
        self.assertEqual(cfg.auth_token, "s3cret")
        self.assertEqual(cfg.qmt_base_url, "http://127.0.0.1:12345")
        self.assertEqual(cfg.qmt_token, "abc")
        self.assertEqual(cfg.request_timeout, 3.5)
        self.assertTrue(cfg.binds_remote)

    def test_invalid_values_fall_back_to_defaults(self):
        cfg = config_from_mapping(
            {"QMT_MCP_PORT": "not-a-port", "QMT_MCP_REQUEST_TIMEOUT": ""}
        )
        self.assertEqual(cfg.port, 9000)
        self.assertEqual(cfg.request_timeout, 10.0)

    def test_base_config_is_preserved(self):
        base = McpConfig(port=8000, qmt_token="base-token")
        cfg = config_from_mapping({"QMT_MCP_AUTH_TOKEN": "t"}, base=base)
        self.assertEqual(cfg.port, 8000)
        self.assertEqual(cfg.qmt_token, "base-token")
        self.assertEqual(cfg.auth_token, "t")

    def test_blank_auth_token_becomes_none(self):
        self.assertIsNone(config_from_mapping({"QMT_MCP_AUTH_TOKEN": "  "}).auth_token)


class McpConfigValidateTest(unittest.TestCase):
    def test_remote_bind_requires_explicit_opt_in(self):
        with self.assertRaises(McpConfigError):
            McpConfig(host="0.0.0.0").validate()

    def test_remote_bind_allowed_with_flag(self):
        cfg = McpConfig(host="0.0.0.0", allow_remote=True).validate()
        self.assertEqual(cfg.host, "0.0.0.0")

    def test_bad_port_rejected(self):
        with self.assertRaises(McpConfigError):
            McpConfig(port=0).validate()
        with self.assertRaises(McpConfigError):
            McpConfig(port=70000).validate()

    def test_bad_qmt_url_rejected(self):
        with self.assertRaises(McpConfigError):
            McpConfig(qmt_base_url="127.0.0.1:10086").validate()

    def test_load_mcp_config_returns_config(self):
        self.assertIsInstance(load_mcp_config(), McpConfig)


if __name__ == "__main__":
    unittest.main()
