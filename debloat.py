#!/usr/bin/env python3
"""Android bloatware remover.

Connects to a phone over ADB, detects the brand, and removes preinstalled
apps for the current user only (`pm uninstall -k --user 0`). Nothing is
deleted from the system partition, so every app can be brought back with
`python debloat.py restore`, and a factory reset restores everything.

No root and no extra Python packages needed. Requires `adb` on PATH.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LISTS_DIR = ROOT / "lists"
BACKUP_DIR = ROOT / "backups"
LEVELS = ("safe", "advanced")

# Never touched, even if they end up in a list by mistake. Removing these
# can leave the phone unable to boot or make calls.
PROTECTED = {
    "android",
    "com.android.systemui",
    "com.android.settings",
    "com.android.phone",
    "com.android.providers.settings",
    "com.android.providers.telephony",
    "com.android.shell",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.vending",
    "com.google.android.gms",
    "com.google.android.gsf",
    "com.android.launcher3",
    "com.sec.android.app.launcher",
    "com.miui.home",
    "com.miui.securitycenter",
    "com.android.server.telecom",
    "com.samsung.android.mobileservice",
    "com.iqoo.secure",
}


@dataclass
class Package:
    id: str
    label: str
    level: str
    source: str
    note: str = ""


class AdbError(RuntimeError):
    pass


class Adb:
    """Thin wrapper around the adb binary. Tests swap `run` for a fake."""

    def __init__(self, serial: str | None = None, binary: str = "adb"):
        self.serial = serial
        self.binary = binary

    def run(self, *args: str) -> str:
        cmd = [self.binary]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except FileNotFoundError:
            raise AdbError(
                "adb not found. Install Android Platform Tools: "
                "https://developer.android.com/tools/releases/platform-tools"
            )
        except subprocess.TimeoutExpired:
            raise AdbError(f"adb timed out: {' '.join(args)}")
        return (out.stdout + out.stderr).strip()

    def shell(self, *args: str) -> str:
        return self.run("shell", *args)

    def devices(self) -> list[str]:
        lines = self.run("devices").splitlines()[1:]
        serials, unauthorized = [], []
        for line in lines:
            parts = line.split()
            if len(parts) >= 2:
                if parts[1] == "device":
                    serials.append(parts[0])
                elif parts[1] == "unauthorized":
                    unauthorized.append(parts[0])
        if not serials and unauthorized:
            raise AdbError(
                "Phone found but not authorized. Unlock it and tap "
                "'Allow' on the USB debugging prompt, then run again."
            )
        return serials

    def prop(self, name: str) -> str:
        return self.shell("getprop", name).strip()

    def installed_packages(self) -> set[str]:
        out = self.shell("pm", "list", "packages", "--user", "0")
        return {l[8:].strip() for l in out.splitlines() if l.startswith("package:")}

    def uninstall(self, pkg: str) -> tuple[bool, str]:
        out = self.shell("pm", "uninstall", "-k", "--user", "0", pkg)
        return "Success" in out, out

    def restore(self, pkg: str) -> tuple[bool, str]:
        out = self.shell("cmd", "package", "install-existing", "--user", "0", pkg)
        ok = "installed for user" in out or "Success" in out
        if not ok:  # older Android (< 8) only has the pm form
            out = self.shell("pm", "install-existing", "--user", "0", pkg)
            ok = "installed for user" in out or "Success" in out
        return ok, out


# ---------------------------------------------------------------- lists

def load_lists(lists_dir: Path = LISTS_DIR) -> dict[str, dict]:
    lists = {}
    for path in sorted(lists_dir.glob("*.json")):
        with path.open(encoding="utf-8") as f:
            lists[path.stem] = json.load(f)
    return lists


def lists_for(manufacturer: str, brand: str, lists: dict[str, dict]) -> list[str]:
    """Pick 'common' plus every brand list that matches the phone."""
    names = (manufacturer + " " + brand).lower()
    chosen = ["common"] if "common" in lists else []
    for key, data in lists.items():
        if key != "common" and any(m in names for m in data.get("manufacturers", [])):
            chosen.append(key)
    return chosen


def build_plan(chosen: list[str], lists: dict[str, dict], installed: set[str],
               level: str) -> list[Package]:
    allowed = LEVELS[: LEVELS.index(level) + 1]
    plan, seen = [], set()
    for key in chosen:
        for p in lists[key]["packages"]:
            pid = p["id"]
            if pid in seen or pid in PROTECTED or pid not in installed:
                continue
            if p.get("level", "safe") not in allowed:
                continue
            seen.add(pid)
            plan.append(Package(pid, p["label"], p.get("level", "safe"), key,
                                p.get("note", "")))
    return plan


# ---------------------------------------------------------------- backup log

def backup_file(serial: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in serial)
    return BACKUP_DIR / f"{safe}.json"


def read_backup(serial: str) -> list[dict]:
    path = backup_file(serial)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_backup(serial: str, entries: list[dict]) -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    backup_file(serial).write_text(json.dumps(entries, indent=2) + "\n",
                                   encoding="utf-8")


# ---------------------------------------------------------------- UI helpers

def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def parse_selection(text: str, count: int) -> set[int]:
    """'1,3,5-7' -> {0,2,4,5,6}. Out-of-range numbers are ignored."""
    picked = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                picked.update(range(a - 1, b))
            else:
                picked.add(int(part) - 1)
        except ValueError:
            continue
    return {i for i in picked if 0 <= i < count}


def print_plan(plan: list[Package]) -> None:
    width = len(str(len(plan)))
    for i, p in enumerate(plan, 1):
        tag = "" if p.level == "safe" else " [advanced]"
        note = f"  - {p.note}" if p.note else ""
        print(f"  {i:>{width}}. {p.label}{tag}\n      {p.id}{note}")


def connect(serial: str | None) -> Adb:
    adb = Adb(serial)
    if not shutil.which(adb.binary):
        raise AdbError(
            "adb not found. Install Android Platform Tools and add it to PATH: "
            "https://developer.android.com/tools/releases/platform-tools"
        )
    devices = adb.devices()
    if serial:
        if serial not in devices:
            raise AdbError(f"Device {serial} not connected.")
    elif not devices:
        raise AdbError(
            "No phone found. Plug it in with USB, turn on USB debugging "
            "(Settings > Developer options), and accept the prompt on the phone."
        )
    elif len(devices) > 1:
        raise AdbError("More than one device connected. Pick one with --serial: "
                       + ", ".join(devices))
    else:
        adb.serial = devices[0]
    return adb


def describe(adb: Adb) -> tuple[str, str]:
    manufacturer = adb.prop("ro.product.manufacturer")
    brand = adb.prop("ro.product.brand")
    model = adb.prop("ro.product.model")
    android = adb.prop("ro.build.version.release")
    print(f"Phone: {manufacturer} {model} (brand: {brand}, Android {android}, "
          f"serial {adb.serial})")
    return manufacturer, brand


# ---------------------------------------------------------------- commands

def cmd_remove(args: argparse.Namespace) -> int:
    adb = connect(args.serial)
    manufacturer, brand = describe(adb)
    lists = load_lists()
    chosen = lists_for(manufacturer, brand, lists)
    print("Using lists: " + ", ".join(lists[k]["name"] for k in chosen))

    plan = build_plan(chosen, lists, adb.installed_packages(), args.level)
    if not plan:
        print("Nothing to remove. Your phone is already clean for this level.")
        return 0

    print(f"\nFound {len(plan)} bloatware app(s) on this phone:\n")
    print_plan(plan)

    if args.dry_run:
        print("\nDry run: nothing was removed.")
        return 0

    if not args.yes:
        skip = ask("\nNumbers to KEEP (e.g. 2,5-7), or press Enter to remove all: ")
        keep = parse_selection(skip, len(plan))
        plan = [p for i, p in enumerate(plan) if i not in keep]
        if not plan:
            print("Nothing selected.")
            return 0
        if ask(f"Remove {len(plan)} app(s)? [y/N] ").lower() not in ("y", "yes"):
            print("Cancelled.")
            return 0

    log = read_backup(adb.serial)
    removed = failed = 0
    for p in plan:
        ok, out = adb.uninstall(p.id)
        if ok:
            removed += 1
            print(f"  removed  {p.label}")
            log.append({"id": p.id, "label": p.label,
                        "removed_at": datetime.now().isoformat(timespec="seconds")})
        else:
            failed += 1
            print(f"  FAILED   {p.label}: {out}")
    if removed:
        write_backup(adb.serial, log)

    print(f"\nDone: {removed} removed, {failed} failed.")
    if removed:
        print("Restart the phone to finish. To undo: python debloat.py restore")
    return 1 if failed else 0


def cmd_restore(args: argparse.Namespace) -> int:
    adb = connect(args.serial)
    describe(adb)
    log = read_backup(adb.serial)

    if args.packages:
        targets = list(args.packages)
    else:
        if not log:
            print("No removal history for this phone. "
                  "Name packages to restore: python debloat.py restore <package>")
            return 0
        print("\nApps removed by this tool:\n")
        for i, e in enumerate(log, 1):
            print(f"  {i}. {e['label']}  ({e['id']})")
        pick = ask("\nNumbers to restore (e.g. 1,3), 'all', or Enter to cancel: ")
        if pick.lower() == "all":
            targets = [e["id"] for e in log]
        else:
            targets = [log[i]["id"] for i in sorted(parse_selection(pick, len(log)))]
        if not targets:
            print("Cancelled.")
            return 0

    failed = 0
    restored = set()
    for pkg in targets:
        ok, out = adb.restore(pkg)
        print(f"  {'restored' if ok else 'FAILED  '}  {pkg}" + ("" if ok else f": {out}"))
        if ok:
            restored.add(pkg)
        else:
            failed += 1
    if restored:
        write_backup(adb.serial, [e for e in log if e["id"] not in restored])
    return 1 if failed else 0


def cmd_list(args: argparse.Namespace) -> int:
    """Show what's in the lists for this phone, and what's still installed."""
    adb = connect(args.serial)
    manufacturer, brand = describe(adb)
    lists = load_lists()
    installed = adb.installed_packages()
    for key in lists_for(manufacturer, brand, lists):
        print(f"\n{lists[key]['name']}:")
        for p in lists[key]["packages"]:
            state = "installed" if p["id"] in installed else "gone     "
            print(f"  [{state}] {p.get('level','safe'):8} {p['label']}  ({p['id']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove bloatware from an Android phone over ADB (no root).")
    parser.add_argument("--serial", help="device serial, if more than one is connected")
    sub = parser.add_subparsers(dest="command")

    rm = sub.add_parser("remove", help="remove bloatware (default)")
    rm.add_argument("--level", choices=LEVELS, default="safe",
                    help="'safe' (default) or 'advanced' to include apps some people use")
    rm.add_argument("--dry-run", action="store_true", help="show what would be removed")
    rm.add_argument("-y", "--yes", action="store_true", help="don't ask, remove all")

    rs = sub.add_parser("restore", help="bring back removed apps")
    rs.add_argument("packages", nargs="*", help="package names (default: pick from history)")

    sub.add_parser("list", help="show the bloatware lists for this phone")

    argv = list(sys.argv[1:] if argv is None else argv)
    if not {"remove", "restore", "list", "-h", "--help"} & set(argv):
        # No command given: default to 'remove', keeping --serial in front.
        front = []
        while argv and argv[0].startswith("--serial"):
            front.append(argv.pop(0))
            if front[-1] == "--serial" and argv:
                front.append(argv.pop(0))
        argv = front + ["remove"] + argv
    args = parser.parse_args(argv)

    try:
        return {"remove": cmd_remove, "restore": cmd_restore,
                "list": cmd_list}[args.command](args)
    except AdbError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
