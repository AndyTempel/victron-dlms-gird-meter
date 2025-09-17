#!/usr/bin/env python3
"""
Validate telegram template files under ./telegrams according to project rules:
- Required info fields are present
- Transformations use valid types and required fields
- If multiple telegrams are defined, info.multiple_telegrams must be true
- Telegram "length" matches number of contents and positions are contiguous
- Telegram content types are valid

Exit code: 0 on success, 1 if any errors were found.
"""
from __future__ import annotations

import glob
import os
import sys
from typing import Any, Dict, List

import yaml

# Types supported by telegram_processor.py
VALID_CONTENT_TYPES = {
    "OctetString",
    "UInt32",
    "UInt16",
    "UInt8",
    "Enum",
    "Boolean",
}

# Transformations supported by telegram_processor.py
VALID_TRANSFORM_TYPES = {
    "MULTIPLY",
    "ADD",
    "SUBTRACT",
    "DIVIDE",
    "REPLACE",
    "TO_INTEGER",
    "TO_STRING",
    "TO_FLOAT",
    "MULTIPLY_IF_KEY",
}

REQUIRED_INFO_FIELDS = {
    "id",
    "name",
    "distributer",
    "country",
    "supported_interfaces",
    "multiple_telegrams",
    "required_keys",
}


def _err(errors: List[str], path: str, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def validate_info(doc: Dict[str, Any], path: str, errors: List[str]) -> None:
    if not isinstance(doc, dict):
        _err(errors, path, "YAML root must be a mapping")
        return

    version = doc.get("version")
    if not isinstance(version, str):
        _err(errors, path, 'Missing or invalid "version" (expected string)')

    info = doc.get("info")
    if not isinstance(info, dict):
        _err(errors, path, 'Missing or invalid "info" (expected mapping)')
        return

    # All required info fields must be present
    missing = [k for k in REQUIRED_INFO_FIELDS if k not in info]
    if missing:
        _err(errors, path, f"Missing required info fields: {', '.join(sorted(missing))}")

    # Validate field types
    if "supported_interfaces" in info and not isinstance(info["supported_interfaces"], list):
        _err(errors, path, "info.supported_interfaces must be a list")
    if "multiple_telegrams" in info and not isinstance(info["multiple_telegrams"], bool):
        _err(errors, path, "info.multiple_telegrams must be a boolean")
    if "required_keys" in info and not isinstance(info["required_keys"], list):
        _err(errors, path, "info.required_keys must be a list")


def validate_transformations(doc: Dict[str, Any], path: str, errors: List[str]) -> None:
    transforms = doc.get("transformations", [])
    if transforms in (None, {}):
        transforms = []

    if not isinstance(transforms, list):
        _err(errors, path, "transformations must be a list when present")
        return

    for i, t in enumerate(transforms):
        if not isinstance(t, dict):
            _err(errors, path, f"transformations[{i}] must be a mapping")
            continue
        ttype = t.get("type")
        if ttype not in VALID_TRANSFORM_TYPES:
            _err(errors, path, f"transformations[{i}].type invalid: {ttype!r}")
            continue
        # Common key check
        if "key" not in t:
            _err(errors, path, f"transformations[{i}] missing required field: key")

        # Per-type requirements
        if ttype in {"MULTIPLY", "ADD", "SUBTRACT", "DIVIDE", "REPLACE"}:
            if "value" not in t:
                _err(errors, path, f"transformations[{i}] type {ttype} requires 'value'")
        if ttype == "MULTIPLY_IF_KEY":
            for req in ("operand", "value", "multiplier", "transform_key"):
                if req not in t:
                    _err(
                        errors, path, f"transformations[{i}] type MULTIPLY_IF_KEY requires '{req}'"
                    )
            # Sanity check operand
            if "operand" in t and t["operand"] not in {"GT", "GTE", "LT", "LTE", "EQ", "NEQ"}:
                _err(errors, path, f"transformations[{i}].operand invalid: {t['operand']!r}")


def validate_telegrams(doc: Dict[str, Any], path: str, errors: List[str]) -> None:
    telegrams = doc.get("telegrams")
    if telegrams is None:
        _err(errors, path, 'Missing "telegrams" list')
        return
    if not isinstance(telegrams, list):
        _err(errors, path, '"telegrams" must be a list')
        return

    info = doc.get("info", {}) if isinstance(doc.get("info"), dict) else {}
    multi_flag = info.get("multiple_telegrams")
    if len(telegrams) > 1 and multi_flag is not True:
        _err(errors, path, "multiple telegrams declared but info.multiple_telegrams is not true")

    for ti, tg in enumerate(telegrams):
        if not isinstance(tg, dict):
            _err(errors, path, f"telegrams[{ti}] must be a mapping")
            continue
        name = tg.get("name")
        if not isinstance(name, str) or not name:
            _err(errors, path, f"telegrams[{ti}].name missing or invalid")
        length = tg.get("length")
        if not isinstance(length, int) or length < 0:
            _err(
                errors,
                path,
                f"telegrams[{ti}].length missing or invalid (expected non-negative int)",
            )
            continue
        contents = tg.get("contents")
        if not isinstance(contents, list):
            _err(errors, path, f"telegrams[{ti}].contents must be a list")
            continue

        # Length must match number of items in contents
        if length != len(contents):
            _err(errors, path, f"telegrams[{ti}] length {length} != contents size {len(contents)}")

        # Positions should be contiguous 0..N-1 and unique
        positions = []
        for ci, c in enumerate(contents):
            if not isinstance(c, dict):
                _err(errors, path, f"telegrams[{ti}].contents[{ci}] must be a mapping")
                continue
            for field in ("position", "name", "type"):
                if field not in c:
                    _err(errors, path, f"telegrams[{ti}].contents[{ci}] missing '{field}'")
            # Validate type is allowed
            ctype = c.get("type")
            if ctype not in VALID_CONTENT_TYPES:
                _err(errors, path, f"telegrams[{ti}].contents[{ci}].type invalid: {ctype!r}")
            # Accumulate positions
            pos = c.get("position")
            if isinstance(pos, int):
                positions.append(pos)

        if positions:
            expected = list(range(len(contents)))
            if sorted(positions) != expected:
                _err(
                    errors,
                    path,
                    f"telegrams[{ti}] positions must be contiguous 0..{len(contents)-1} and unique (got {sorted(positions)})",
                )


def main() -> int:
    base = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    pattern = os.path.join(base, "telegrams", "*.yml")
    files = sorted(glob.glob(pattern))

    if not files:
        print("No telegram YAML files found.")
        return 0

    errors: List[str] = []

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
        except Exception as ex:
            _err(errors, path, f"YAML load error: {ex}")
            continue

        validate_info(doc, path, errors)
        validate_transformations(doc, path, errors)
        validate_telegrams(doc, path, errors)

    if errors:
        print("Telegram template validation failed:")
        for e in errors:
            print(f" - {e}")
        return 1

    print(f"Validated {len(files)} telegram YAML files successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
