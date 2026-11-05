import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import debloat  # noqa: E402


class FakeAdb(debloat.Adb):
    """Pretends to be a phone. Records every shell command."""

    def __init__(self, props, installed):
        super().__init__("FAKE123")
        self.props = props
        self.installed = set(installed)
        self.calls = []

    def run(self, *args):
        self.calls.append(args)
        if args == ("devices",):
            return "List of devices attached\nFAKE123\tdevice"
        cmd = args[1:]
        if cmd[0] == "getprop":
            return self.props.get(cmd[1], "")
        if cmd[:3] == ("pm", "list", "packages"):
            return "\n".join("package:" + p for p in sorted(self.installed))
        if cmd[:2] == ("pm", "uninstall"):
            self.installed.discard(cmd[-1])
            return "Success"
        if cmd[:3] == ("cmd", "package", "install-existing"):
            self.installed.add(cmd[-1])
            return f"Package {cmd[-1]} installed for user: 0"
        return ""


SAMSUNG = {"ro.product.manufacturer": "samsung", "ro.product.brand": "samsung",
           "ro.product.model": "SM-A546B", "ro.build.version.release": "14"}


class ListTests(unittest.TestCase):
    def test_lists_are_valid(self):
        lists = debloat.load_lists()
        self.assertIn("common", lists)
        for key, data in lists.items():
            ids = [p["id"] for p in data["packages"]]
            self.assertEqual(len(ids), len(set(ids)), f"duplicate in {key}")
            for p in data["packages"]:
                self.assertIn(p["level"], debloat.LEVELS)
                self.assertNotIn(p["id"], debloat.PROTECTED, f"{p['id']} is protected")

    def test_brand_detection(self):
        lists = debloat.load_lists()
        self.assertEqual(debloat.lists_for("samsung", "samsung", lists), ["common", "samsung"])
        self.assertEqual(debloat.lists_for("Xiaomi", "Redmi", lists), ["common", "xiaomi"])
        self.assertEqual(debloat.lists_for("OnePlus", "OnePlus", lists), ["common", "oppo"])
        self.assertEqual(debloat.lists_for("Google", "google", lists), ["common"])

    def test_plan_respects_level_and_installed(self):
        lists = debloat.load_lists()
        installed = {"com.samsung.android.app.tips", "com.sec.android.app.sbrowser",
                     "com.android.systemui"}
        safe = debloat.build_plan(["common", "samsung"], lists, installed, "safe")
        self.assertEqual([p.id for p in safe], ["com.samsung.android.app.tips"])
        adv = debloat.build_plan(["common", "samsung"], lists, installed, "advanced")
        self.assertEqual({p.id for p in adv},
                         {"com.samsung.android.app.tips", "com.sec.android.app.sbrowser"})

    def test_parse_selection(self):
        self.assertEqual(debloat.parse_selection("1, 3-4, 99, x", 5), {0, 2, 3})
        self.assertEqual(debloat.parse_selection("", 5), set())


class FlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(debloat, "BACKUP_DIR", Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.phone = FakeAdb(SAMSUNG, {"com.samsung.android.app.tips",
                                       "com.facebook.katana", "com.android.systemui"})
        patcher = mock.patch.object(debloat, "connect", return_value=self.phone)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv, answers=()):
        with mock.patch.object(debloat, "ask", side_effect=list(answers)), \
             mock.patch("sys.stdout"):
            return debloat.main(list(argv))

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_cli("--dry-run"), 0)
        self.assertIn("com.facebook.katana", self.phone.installed)

    def test_remove_keep_and_restore(self):
        # Keep item 1 (Facebook, from the common list), remove the rest.
        self.assertEqual(self.run_cli("remove", answers=["1", "y"]), 0)
        self.assertIn("com.facebook.katana", self.phone.installed)
        self.assertNotIn("com.samsung.android.app.tips", self.phone.installed)
        self.assertIn("com.android.systemui", self.phone.installed)

        log = json.loads(debloat.backup_file("FAKE123").read_text())
        self.assertEqual([e["id"] for e in log], ["com.samsung.android.app.tips"])

        self.assertEqual(self.run_cli("restore", "all"), 0)
        self.assertIn("com.samsung.android.app.tips", self.phone.installed)
        self.assertEqual(json.loads(debloat.backup_file("FAKE123").read_text()), [])

    def test_cancel(self):
        self.run_cli(answers=["", "n"])
        self.assertIn("com.facebook.katana", self.phone.installed)

    def test_default_command_with_options(self):
        self.assertEqual(self.run_cli("--serial", "FAKE123", "--level", "advanced",
                                      "--dry-run"), 0)


if __name__ == "__main__":
    unittest.main()
