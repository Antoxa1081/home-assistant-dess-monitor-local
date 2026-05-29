# Technical Debt

Tracker for known minor issues and rough edges that don't block functionality
but should eventually be cleaned up. Items here are deliberate "fix-later"
decisions — not urgent bugs.

---

## ✅ RESOLVED — PI30 QPIGS: CRC-byte bleed into last field

**Resolved:** `decode_qpigs` now sanitizes `device_status_bits_b7_b0` and
`device_status_bits_b10_b8` via `_clean_bits()` (strips non-`0/1` chars,
clamps to width). Regression test:
`tests/test_voltronic_decoder.py::TestDecodeQpigs::test_status_bits_crc_bleed_sanitized`.

Original report retained below for context.

**Where:** `custom_components/dess_monitor_local/api/decoders/voltronic.py`, function `decode_qpigs`.

**Symptom:** The last QPIGS field — `device_status_bits_b10_b8` — sometimes
contains an extra trailing character that's actually a CRC byte landing in
the printable-ASCII range. Example from a real Anern 4200 diagnostic dump:

```
raw_ascii: "... 01539 110\xfes"
parsed:    device_status_bits_b10_b8 = "110s"
```

Per protocol, `b10_b8` is three `0/1` characters. The trailing `s` (or `U`,
`&`, `r`, ...) is whichever byte of the 2-byte XMODEM CRC happens to be a
printable ASCII char (0x20-0x7E). On a frame ending with `\xfe\x73`, the
`\xfe` is dropped by `errors="ignore"` in the decode step, but `\x73 = 's'`
survives and gets attached to the preceding token by `split()`.

**Impact today:** Cosmetic only. The main device-status sensor reads from
`b7_b0` (clean — bleed only affects the last field). No active parser
consumes `b10_b8`.

**Risk if left:** If a future parser is added for `b10_b8` (status flags
for charging direction / SCC active / etc.), it will trip on the stray
trailing character. `int("110s", 2)` raises `ValueError`.

**Fix sketch:**

```python
# At the end of decode_qpigs, sanitize the trailing status field.
if "device_status_bits_b10_b8" in result:
    raw = result["device_status_bits_b10_b8"]
    # b10_b8 is exactly 3 binary digits per PI30 spec.
    cleaned = "".join(c for c in raw if c in "01")[:3]
    result["device_status_bits_b10_b8"] = cleaned.ljust(3, "0")
```

Apply the same treatment to any other trailing-bit field if the spec drift
ever puts CRC adjacent to other binary tokens.

**Decision when to do it:** When the first consumer of `b10_b8` is added,
or sooner if we want to clean up the diagnostic dumps so the bleed isn't
visible to users.
