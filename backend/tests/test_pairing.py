import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as diakopad_app
import pairing
import storage


class PairingManagerTests(unittest.TestCase):
    def test_code_has_six_digits_and_expires(self):
        mgr = pairing.PairingManager()
        req = mgr.create("Moto G", "192.168.0.9", now=1000)
        self.assertRegex(req.code, r"^\d{6}$")
        self.assertEqual(len(mgr.pending(now=1000)), 1)
        self.assertEqual(mgr.pending(now=1000 + pairing.CODE_TTL_S), [])
        with self.assertRaises(pairing.PairingError) as ctx:
            mgr.confirm(req.request_id, req.code, now=1000 + pairing.CODE_TTL_S)
        self.assertEqual(ctx.exception.status, 410)

    def test_wrong_code_counts_attempts_then_invalidates(self):
        mgr = pairing.PairingManager()
        req = mgr.create("Moto G", "192.168.0.9", now=0)
        wrong = "000000" if req.code != "000000" else "111111"
        for _ in range(pairing.MAX_ATTEMPTS - 1):
            with self.assertRaises(pairing.PairingError) as ctx:
                mgr.confirm(req.request_id, wrong, now=1)
            self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(pairing.PairingError) as ctx:
            mgr.confirm(req.request_id, wrong, now=1)
        self.assertEqual(ctx.exception.status, 410)
        with self.assertRaises(pairing.PairingError):
            mgr.confirm(req.request_id, req.code, now=1)

    def test_same_host_replaces_and_pending_is_capped(self):
        mgr = pairing.PairingManager()
        first = mgr.create("A", "10.0.0.2", now=0)
        mgr.create("A de novo", "10.0.0.2", now=1)
        self.assertNotIn(first.request_id, mgr.requests)
        mgr.create("B", "10.0.0.3", now=2)
        mgr.create("C", "10.0.0.4", now=3)
        with self.assertRaises(pairing.PairingError) as ctx:
            mgr.create("D", "10.0.0.5", now=4)
        self.assertEqual(ctx.exception.status, 429)

    def test_device_name_is_cleaned(self):
        self.assertEqual(pairing.clean_device_name("  Moto\n  G \x00 "), "Moto G")
        self.assertEqual(pairing.clean_device_name(""), "Celular")
        self.assertEqual(len(pairing.clean_device_name("x" * 500)), pairing.DEVICE_NAME_MAX)

    def test_advertiser_stays_quiet_on_localhost(self):
        adv = pairing.Advertiser()
        with patch.dict(os.environ, {"DIAKOPAD_HOST": "127.0.0.1"}), patch("pairing.subprocess.Popen") as popen:
            adv.start("0.1.3")
        popen.assert_not_called()

    def test_advertiser_publishes_service_when_listening_on_network(self):
        adv = pairing.Advertiser()
        with (
            patch.dict(os.environ, {"DIAKOPAD_HOST": "0.0.0.0", "DIAKOPAD_NAME": "Palco"}),
            patch("pairing.shutil.which", return_value="/usr/bin/avahi-publish-service"),
            patch("pairing.subprocess.Popen") as popen,
        ):
            adv.start("0.1.3")
        args = popen.call_args.args[0]
        self.assertEqual(args[1:4], ["Palco", "_diakopad._tcp", "8080"])


class ListenAddressTests(unittest.TestCase):
    def test_reads_uvicorn_cli_when_env_is_missing(self):
        argv = ["uvicorn", "app:app", "--host", "0.0.0.0", "--port=9090"]
        env = {k: v for k, v in os.environ.items() if k not in ("DIAKOPAD_HOST", "DIAKOPAD_PORT")}
        with patch.dict(os.environ, env, clear=True), patch("pairing.sys.argv", argv):
            self.assertEqual(pairing.listen_host(), "0.0.0.0")
            self.assertEqual(pairing.listen_port(), "9090")

    def test_env_wins_and_default_is_localhost(self):
        with patch.dict(os.environ, {"DIAKOPAD_HOST": "192.168.0.5"}), patch("pairing.sys.argv", ["uvicorn", "--host", "0.0.0.0"]):
            self.assertEqual(pairing.listen_host(), "192.168.0.5")
        env = {k: v for k, v in os.environ.items() if k != "DIAKOPAD_HOST"}
        with patch.dict(os.environ, env, clear=True), patch("pairing.sys.argv", ["uvicorn", "app:app"]):
            self.assertEqual(pairing.listen_host(), "127.0.0.1")


class PairingApiTests(unittest.TestCase):
    def setUp(self):
        self._original_db_path = storage.DB_PATH
        self._temp_dir = tempfile.TemporaryDirectory()
        storage.DB_PATH = Path(self._temp_dir.name) / "test.db"
        storage.init_db()
        env = patch.dict(os.environ, {"DIAKOPAD_TOKEN": "segredo-mestre"})
        env.start()
        self.addCleanup(env.stop)
        mgr = patch.object(diakopad_app, "_pairing", pairing.PairingManager())
        mgr.start()
        self.addCleanup(mgr.stop)
        self.client = TestClient(diakopad_app.app)

    def tearDown(self):
        storage.DB_PATH = self._original_db_path
        self._temp_dir.cleanup()

    def _pair(self):
        res = self.client.post("/api/pair/request", json={"device_name": "Moto G"})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("code", res.json())  # o código nunca vai para quem pediu
        request_id = res.json()["request_id"]
        code = diakopad_app._pairing.requests[request_id].code
        return request_id, code

    def test_public_routes_work_without_token_and_others_do_not(self):
        self.assertEqual(self.client.get("/api/pair/info").json()["service"], "diakopad")
        self.assertEqual(self.client.get("/api/pads").status_code, 401)
        self.assertEqual(self.client.get("/api/paired-devices").status_code, 401)

    def test_correct_code_gives_device_token_that_authorizes(self):
        request_id, code = self._pair()
        res = self.client.post("/api/pair/confirm", json={"request_id": request_id, "code": code})
        self.assertEqual(res.status_code, 200)
        token = res.json()["token"]
        self.client.cookies.set("diakopad_token", token)
        self.assertEqual(self.client.get("/api/pads").status_code, 200)
        devices = self.client.get("/api/paired-devices").json()
        self.assertEqual([d["name"] for d in devices], ["Moto G"])
        # O banco guarda só o hash.
        self.assertIsNone(storage.find_paired_device(token))

    def test_wrong_code_is_rejected(self):
        request_id, code = self._pair()
        wrong = "000000" if code != "000000" else "111111"
        res = self.client.post("/api/pair/confirm", json={"request_id": request_id, "code": wrong})
        self.assertEqual(res.status_code, 403)

    def test_revoked_device_loses_access(self):
        request_id, code = self._pair()
        token = self.client.post("/api/pair/confirm", json={"request_id": request_id, "code": code}).json()["token"]
        device_id = storage.list_paired_devices()[0]["id"]
        self.client.cookies.set("diakopad_token", "segredo-mestre")
        self.assertEqual(self.client.delete(f"/api/paired-devices/{device_id}").status_code, 200)
        self.client.cookies.set("diakopad_token", token)
        self.assertEqual(self.client.get("/api/pads").status_code, 401)

    def test_laptop_can_reject_request(self):
        request_id, _code = self._pair()
        self.client.cookies.set("diakopad_token", "segredo-mestre")
        self.assertEqual(self.client.post(f"/api/pair/{request_id}/reject").status_code, 200)
        self.client.cookies.clear()
        res = self.client.post("/api/pair/confirm", json={"request_id": request_id, "code": "123456"})
        self.assertEqual(res.status_code, 410)

    def test_non_ascii_cookie_does_not_crash(self):
        self.assertFalse(diakopad_app._is_authorized("192.168.0.20", "çã"))


if __name__ == "__main__":
    unittest.main()
