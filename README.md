# bloatware-remover

Removes preinstalled junk apps from Android phones over USB. It works out
the brand by itself (Samsung, Xiaomi/Redmi/POCO, Oppo/Realme/OnePlus,
Vivo/iQOO, Huawei/Honor, Motorola) and adds a common list (Facebook,
Netflix, Office, and so on) for every phone.

- **No root needed.** Apps are removed for your user only
  (`pm uninstall -k --user 0`). The system files are left alone.
- **Can be undone.** `python debloat.py restore` brings apps back, and a
  factory reset restores everything.
- **Only removes what's installed.** It shows you the list first and asks before removing anything.
- **Core apps are protected.** It never touches the system UI, Settings, Phone, Play Store,
  Play Services or launchers.

## Setup (once)

1. Install **Python 3.8+** on your computer: https://www.python.org/downloads/
2. Install **Android Platform Tools** (this gives you `adb`) and add it to PATH:
   https://developer.android.com/tools/releases/platform-tools
3. On the phone, turn on **Developer options**: Settings > About phone,
   tap **Build number** 7 times. On Xiaomi, tap **MIUI/OS version** instead.
4. In Developer options, turn on **USB debugging**.
   On Xiaomi, also turn on **USB debugging (Security settings)**.
5. Connect the phone with USB and tap **Allow** on the prompt.

## Use

```bash
python debloat.py --dry-run          # just show what it would remove
python debloat.py                    # remove (asks which to keep first)
python debloat.py --level advanced   # also offer apps some people use (browser, Pay, Game Launcher...)
python debloat.py list               # show the lists for your phone and what's still installed
python debloat.py restore            # bring back apps (pick from history)
python debloat.py restore com.samsung.android.app.tips
```

When it asks for numbers to keep, type them like `2,5-7`, or press Enter to remove them all.
Restart the phone afterwards.

Levels:
- `safe` (default): ads, analytics, duplicate stores, and promo apps. Removing them has no side effects.
- `advanced`: apps that can be useful, such as the brand's browser, payment apps,
  Game Launcher and Galaxy Wearable. Each one has a note on what it breaks.

## Adding apps to the lists

The lists are plain JSON files in `lists/`. Each brand file has a
`manufacturers` field that is matched against the phone's manufacturer and brand.
To add an app, add an entry:

```json
{ "id": "com.example.bloat", "label": "Example", "level": "safe", "note": "optional" }
```

Find package names with `adb shell pm list packages | grep <word>`.

## Tests

```bash
python -m unittest discover -s tests
```

## Notes

- Some apps come back after a big system update. Run the tool again.
- Removal history is saved per phone in `backups/` so `restore` knows what to bring back.
- If the phone acts up, run `restore all`, or factory reset as a last resort.
