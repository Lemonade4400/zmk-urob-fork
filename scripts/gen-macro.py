#!/usr/bin/env python3
"""Generate a ZMK typing macro (.dtsi) from a literal string read on stdin.

The string is never passed on argv so it does not show up in `ps` output.

Usage:  printf '%s' "$secret" | python3 scripts/gen-macro.py type_password
"""

import sys

# Letters and digits sit in the same physical positions on US and IT layouts,
# so these are safe regardless of the host keyboard layout.
DIGITS = {
    "0": "N0", "1": "N1", "2": "N2", "3": "N3", "4": "N4",
    "5": "N5", "6": "N6", "7": "N7", "8": "N8", "9": "N9",
}

# US-layout symbol names. The host must be on a US layout for these to type
# what they say -- see the warning emitted below.
SYMBOLS = {
    " ": "SPACE", "!": "EXCL", '"': "DQT", "#": "HASH", "$": "DLLR",
    "%": "PRCNT", "&": "AMPS", "'": "SQT", "(": "LPAR", ")": "RPAR",
    "*": "STAR", "+": "PLUS", ",": "COMMA", "-": "MINUS", ".": "DOT",
    "/": "FSLH", ":": "COLON", ";": "SEMI", "<": "LT", "=": "EQUAL",
    ">": "GT", "?": "QMARK", "@": "AT", "[": "LBKT", "\\": "BSLH",
    "]": "RBKT", "^": "CARET", "_": "UNDER", "`": "GRAVE", "{": "LBRC",
    "|": "PIPE", "}": "RBRC", "~": "TILDE",
}


def keycode(char):
    if char.isalpha() and char.isascii():
        upper = char.upper()
        return f"LS({upper})" if char.isupper() else upper
    if char in DIGITS:
        return DIGITS[char]
    if char in SYMBOLS:
        return SYMBOLS[char]
    raise SystemExit(f"gen-macro: unsupported character {char!r}")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <macro-name>  (string on stdin)")
    name = sys.argv[1]

    text = sys.stdin.read().rstrip("\n")
    if not text:
        raise SystemExit("gen-macro: empty input on stdin")

    if any(c in SYMBOLS and c != " " for c in text):
        print(
            "gen-macro: warning: string contains symbols, which assume a US "
            "layout on the host -- verify what actually gets typed",
            file=sys.stderr,
        )

    bindings = " ".join(f"&kp {keycode(c)}" for c in text)
    print(f"// Auto-generated at build time - do not edit or commit")
    print(f"ZMK_MACRO({name},")
    print("    wait-ms = <10>;")
    print("    tap-ms = <10>;")
    print(f"    bindings = <{bindings}>;")
    print(")")


if __name__ == "__main__":
    main()
