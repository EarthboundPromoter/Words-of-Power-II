#!/usr/bin/env python3
"""Enable the Words of Power screen reader mod without using the visual Mods menu.

Why this exists:
    Rift Wizard 3's Steam Workshop update made mods opt-in -- a mod only loads if
    its name is in options2.dat -> 'enabled_mods'. That list is normally edited
    through the in-game Mods menu, which a blind player cannot read until the
    screen reader is already loaded. This tool breaks that catch-22 by adding
    'screen_reader' to enabled_mods directly.

Usage:
    Close Rift Wizard 3 first (the game re-saves options on exit and would
    overwrite this change). Then run the tool from anywhere -- it locates your
    Steam copy of the game automatically (and still works if run from inside the
    install). Built as a standalone .exe it needs nothing installed; as a script
    run:  python enable_screen_reader.py
    Pass --disable to turn the mod back off, or --options <path> to point it at a
    specific options2.dat if auto-detection ever misses.

Safety:
    options2.dat is a plain pickle of builtin types only. This tool reads the
    existing settings, appends one string, and writes them back -- keybinds,
    volumes, and other options are preserved.
"""

import argparse
import os
import pickle
import re
import sys

MOD_NAME = "screen_reader"
OPTIONS_FILENAME = "options2.dat"
# Protocol 4 is readable by the game's bundled Python 3.8 (and everything newer).
PICKLE_PROTOCOL = 4

# Rift Wizard 3 is sold only on Steam (solo dev/publisher Dylan White; no
# GOG/itch/Epic listing). These constants let us locate the install from
# anywhere on the machine by asking Steam's own library records, so this tool
# no longer has to live inside the game folder to find options2.dat.
RW3_STEAM_APPID = "4366330"
RW3_DEFAULT_DIRNAME = "Rift Wizard 3"
# A file that must sit beside options2.dat in a real RW3 install. We confirm a
# candidate folder against it before writing, so we never touch the wrong game.
RW3_SENTINEL = "RiftWizard3.py"


def _search_roots():
    """Directories to start the upward search from.

    When frozen by PyInstaller, __file__ points into a temp extraction dir, so we
    must use the real .exe location (sys.executable) instead.
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        try:
            roots.append(os.path.dirname(os.path.abspath(__file__)))
        except NameError:
            pass
    roots.append(os.getcwd())
    return roots


def _steam_roots():
    """Steam install roots, read from the Windows registry (best-effort)."""
    roots = []
    try:
        import winreg
    except ImportError:
        return roots  # Not on Windows -- caller falls back to other strategies.
    # SteamPath (per-user, set by the running client) is the most reliable;
    # the InstallPath keys cover machines where only the installer ran.
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, subkey, value in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, value)
        except OSError:
            continue
        if raw:
            root = os.path.normpath(raw)
            if not any(os.path.normcase(root) == os.path.normcase(r) for r in roots):
                roots.append(root)
    return roots


def _steam_library_dirs(steam_root):
    """Every Steam library 'path' listed in libraryfolders.vdf, plus the root."""
    libs = [steam_root]
    vdf = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libs
    # The .vdf stores Windows paths with doubled backslashes, e.g.
    #   "path"  "D:\\SteamLibrary"
    for raw in re.findall(r'"path"\s*"([^"]+)"', text):
        lib = os.path.normpath(raw.replace("\\\\", "\\"))
        if lib not in libs:
            libs.append(lib)
    return libs


def _rw3_installdir(library_dir):
    """Folder name RW3 is installed under in this library (from its manifest)."""
    manifest = os.path.join(
        library_dir, "steamapps", "appmanifest_%s.acf" % RW3_STEAM_APPID
    )
    try:
        with open(manifest, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    match = re.search(r'"installdir"\s*"([^"]+)"', text)
    return match.group(1) if match else None


def _find_via_steam():
    """Locate RW3's options2.dat by walking Steam's own library records."""
    for steam_root in _steam_roots():
        for lib in _steam_library_dirs(steam_root):
            installdir = _rw3_installdir(lib) or RW3_DEFAULT_DIRNAME
            game_dir = os.path.join(lib, "steamapps", "common", installdir)
            candidate = os.path.join(game_dir, OPTIONS_FILENAME)
            # Require the RW3 sentinel too, so we never write to some unrelated
            # folder that merely happens to contain an options2.dat.
            if os.path.isfile(candidate) and os.path.isfile(
                os.path.join(game_dir, RW3_SENTINEL)
            ):
                return candidate
    return None


def find_options_file():
    """Find options2.dat: walk up from the tool, then ask Steam."""
    # 1. Upward walk -- works when the tool lives inside the install tree.
    for start in _search_roots():
        cur = start
        while True:
            candidate = os.path.join(cur, OPTIONS_FILENAME)
            if os.path.isfile(candidate):
                return candidate
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
    # 2. Steam library lookup -- works when the tool lives anywhere.
    return _find_via_steam()


def _pause():
    """Keep the console open after a double-click so the result can be read/heard."""
    try:
        input("\nPress Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass


def run(disable, options_path):
    path = options_path or find_options_file()
    if not path or not os.path.isfile(path):
        print("ERROR: Could not find options2.dat.")
        print("Auto-detection looks for a Steam copy of Rift Wizard 3. If you")
        print("installed it elsewhere, run this tool from inside the game folder,")
        print("or pass --options <path to options2.dat>.")
        return 1

    try:
        with open(path, "rb") as f:
            options = pickle.load(f)
    except Exception as exc:
        print("ERROR: Could not read %s: %s" % (path, exc))
        return 1

    if not isinstance(options, dict):
        print("ERROR: Unexpected options format in %s." % path)
        return 1

    enabled = options.get("enabled_mods")
    if not isinstance(enabled, list):
        enabled = []

    if disable:
        if MOD_NAME in enabled:
            enabled = [m for m in enabled if m != MOD_NAME]
            options["enabled_mods"] = enabled
            with open(path, "wb") as f:
                pickle.dump(options, f, protocol=PICKLE_PROTOCOL)
            print("Done. The screen reader mod is now DISABLED.")
        else:
            print("The screen reader mod was already disabled. No change made.")
        print("Enabled mods: %s" % (enabled or "(none)"))
        return 0

    if MOD_NAME in enabled:
        print("The screen reader mod is already enabled. No change needed.")
        print("Enabled mods: %s" % enabled)
        return 0

    enabled.append(MOD_NAME)
    options["enabled_mods"] = enabled
    with open(path, "wb") as f:
        pickle.dump(options, f, protocol=PICKLE_PROTOCOL)

    print("Done. The screen reader mod is now ENABLED.")
    print("Updated: %s" % path)
    print("Enabled mods: %s" % enabled)
    print("Make sure Rift Wizard 3 is fully closed, then start it --")
    print("the screen reader will speak on launch.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Enable or disable the Words of Power screen reader mod for Rift Wizard 3."
    )
    parser.add_argument(
        "--disable", action="store_true",
        help="Remove the mod from enabled_mods instead of adding it.",
    )
    parser.add_argument(
        "--options", help="Explicit path to options2.dat (otherwise auto-detected).",
    )
    parser.add_argument(
        "--no-pause", action="store_true",
        help="Do not wait for Enter before exiting (for use inside other scripts).",
    )
    args = parser.parse_args()

    code = run(args.disable, args.options)
    if not args.no_pause:
        _pause()
    return code


if __name__ == "__main__":
    sys.exit(main())
