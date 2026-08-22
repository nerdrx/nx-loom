"""Headless test runner. Invoked by run_all.sh inside Blender."""

import importlib
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    only = os.environ.get("NXL_ONLY", "")
    mods = sorted(
        m[:-3] for m in os.listdir(HERE)
        if re.match(r"test_\d+_.*\.py$", m) and (not only or only in m)
    )
    if not mods:
        print(f"!! no test modules matched NXL_ONLY={only!r}")
        return 2

    passed = failed = 0
    for name in mods:
        try:
            mod = importlib.import_module(name)
        except Exception:
            print(f"-- {name}\n   IMPORT FAILED")
            traceback.print_exc()
            failed += 1
            continue
        print(f"-- {name}")
        try:
            results = mod.run()
        except Exception:
            print("   RAISED")
            traceback.print_exc()
            failed += 1
            continue
        for label, ok, msg in results:
            print(f"   {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{msg}]" if msg else ""))
            if ok:
                passed += 1
            else:
                failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
