import importlib.util
import os
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(ROOT, "rm-send.py")


spec = importlib.util.spec_from_file_location("rm_send", MODULE_PATH)
rm_send = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm_send)


class RmapiConfigTests(unittest.TestCase):
    def test_macos_application_support_config_enables_cloud(self):
        expected = os.path.expanduser(
            "~/Library/Application Support/rmapi/rmapi.conf"
        )

        def exists(path):
            return path == expected

        with patch.object(rm_send.os.path, "exists", side_effect=exists):
            self.assertTrue(rm_send.rmapi_ready())


if __name__ == "__main__":
    unittest.main()
