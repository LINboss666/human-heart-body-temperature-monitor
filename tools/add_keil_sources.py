#!/usr/bin/env python3
"""
Add the application and vendored sources to the Keil MDK-ARM target.

Why a script rather than hand-editing the XML: the same set of groups has to be
applied to a 36 KB uvprojx that CubeMX owns, and the alternative is a manual
re-add after every regeneration. The script is idempotent - it replaces groups it
previously added and leaves CubeMX's own untouched - so re-running it after a
regeneration is one command instead of a search for what got dropped.

This does NOT conflict with the .ioc being the source of truth for generated code:
the .ioc lists what CubeMX generates, and these are hand-written modules that
CubeMX has never heard of. The consequence, recorded in docs/PHASE1_REVIEW_HANDOFF.md,
is that a CubeMX regeneration wipes these groups and this script must be re-run.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJ = ROOT / "MDK-ARM" / "Human Heart and Body Temperature Monitor.uvprojx"

MARKER = "tools/add_keil_sources.py"

# (group name, [files]) - paths are relative to MDK-ARM/, as Keil stores them.
def collect_groups():
    app_src = sorted((ROOT / "App").rglob("*.c"))
    files = [f"../App/{p.relative_to(ROOT / 'App').as_posix()}" for p in app_src]

    ui_runtime = ROOT / "ThirdParty" / "kk_ui"
    ui_files = [f"../ThirdParty/kk_ui/src/{p.name}"
                for p in sorted((ui_runtime / "src").glob("*.c"))]

    oled = ROOT / "ThirdParty" / "kk_oled"
    oled_files = [f"../ThirdParty/kk_oled/graphics/{p.name}"
                  for p in sorted((oled / "graphics").glob("*.c"))]
    oled_files += [f"../ThirdParty/kk_oled/driver/{p.name}"
                   for p in sorted((oled / "driver").glob("*.c"))]

    return [
        ("Application/App", files),
        ("ThirdParty/kk_ui", ui_files),
        ("ThirdParty/kk_oled", oled_files),
    ]

INCLUDE_DIRS = [
    "../App",
    "../App/config",
    "../App/acquisition",
    "../App/buttons",
    "../App/diagnostics",
    "../App/display",
    "../App/ecg",
    "../App/protocol",
    "../App/rtc_service",
    "../App/temperature",
    "../App/ui",
    "../ThirdParty/kk_ui/include",
    "../ThirdParty/kk_ui/src",
    "../ThirdParty/kk_oled/include",
    "../ThirdParty/kk_oled/graphics",
    "../ThirdParty/kk_oled/driver",
]


def group_xml(name: str, files) -> str:
    body = "".join(
        f'            <File>\n'
        f'              <FileName>{Path(f).name}</FileName>\n'
        f'              <FileType>1</FileType>\n'
        f'              <FilePath>{f}</FilePath>\n'
        f'            </File>\n'
        for f in files)
    return (f'        <Group>\n'
            f'          <GroupName>{name}</GroupName>\n'
            f'          <Files>\n{body}'
            f'          </Files>\n'
            f'        </Group>\n')


def main() -> int:
    text = PROJ.read_text(encoding="utf-8")
    original = text

    # Strip anything this script added before, so re-running is idempotent.
    for name, _ in collect_groups():
        text = re.sub(
            r'        <Group>\s*<GroupName>' + re.escape(name) +
            r'</GroupName>.*?\n        </Group>\n', '', text, flags=re.S)

    groups = "".join(group_xml(n, f) for n, f in collect_groups())
    idx = text.rfind("      </Groups>")
    if idx == -1:
        print("no </Groups> element found - refusing to guess the project shape",
              file=sys.stderr)
        return 1
    text = text[:idx] + groups + text[idx:]

    # Include paths: append to the existing target-level IncludePath element.
    def merge(m):
        existing = m.group(1).strip()
        parts = [p for p in existing.split(";") if p]
        for d in INCLUDE_DIRS:
            if d not in parts:
                parts.append(d)
        return "<IncludePath>" + ";".join(parts) + "</IncludePath>"

    text, n_inc = re.subn(r"<IncludePath>([^<]*)</IncludePath>", merge, text, count=1)

    total = sum(len(f) for _, f in collect_groups())
    PROJ.write_text(text, encoding="utf-8", newline="")
    print(f"{MARKER}: {len(collect_groups())} groups, {total} source files, "
          f"{n_inc} include-path element updated")
    for name, files in collect_groups():
        print(f"  {name}: {len(files)}")
    print("changed" if text != original else "NO CHANGE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
