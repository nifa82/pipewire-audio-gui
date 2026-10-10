#!/usr/bin/env python3
"""Validate config-opts in a Flatpak manifest against upstream meson_options.txt.

Catches the common mistake of passing booleans to meson "feature" options, which
only accept enabled/disabled/auto.

Usage:
    validate-meson-opts.py <manifest.json> <meson_options.txt>
    validate-meson-opts.py <manifest.json> --extract <pipewire.tar.bz2>
"""

import json
import re
import sys
import tarfile


def load_types(meson_options_text):
    flat = re.sub(r"\s+", " ", meson_options_text)
    types = {}
    for m in re.finditer(r"option\(\s*'([\w-]+)'\s*,(.*?)\)\s*(?:,| )", flat):
        t = re.search(r"type\s*:\s*'(\w+)'", m.group(2))
        types[m.group(1)] = t.group(1) if t else "boolean"
    return types


def extract_options_from_tar(path, member_suffix="meson_options.txt"):
    with tarfile.open(path) as tf:
        for name in tf.getnames():
            if name.endswith(member_suffix):
                fh = tf.extractfile(name)
                if fh:
                    return fh.read().decode("utf-8", "replace")
    return None


def value_is_valid(opt_type, value):
    if opt_type == "feature":
        return value in ("enabled", "disabled", "auto")
    if opt_type == "boolean":
        return value in ("true", "false")
    return True


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    manifest_path = sys.argv[1]
    with open(manifest_path) as f:
        manifest = json.load(f)

    if sys.argv[2] == "--extract":
        if len(sys.argv) < 4:
            print("error: --extract needs a tarball path")
            return 2
        text = extract_options_from_tar(sys.argv[3])
        if text is None:
            print("error: no meson_options.txt found in archive")
            return 2
    else:
        with open(sys.argv[2]) as f:
            text = f.read()

    types = load_types(text)

    problems = 0
    checked = 0
    for module in manifest.get("modules", []):
        for opt in module.get("config-opts", []):
            if not opt.startswith("-D"):
                continue
            body = opt[2:]
            if "=" not in body:
                continue
            name, value = body.split("=", 1)
            checked += 1
            opt_type = types.get(name)
            if opt_type is None:
                print("FAIL %-26s unknown option" % name)
                problems += 1
            elif not value_is_valid(opt_type, value):
                print(
                    "FAIL %-26s type=%s value=%r"
                    % (name, opt_type, value)
                )
                problems += 1
            else:
                print("PASS %-26s type=%s value=%r" % (name, opt_type, value))

    print("\n%d option(s) checked, %d problem(s)" % (checked, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())