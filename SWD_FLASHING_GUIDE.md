# SWD Flashing Guide for nice!nano v2 (Right Half)

The right nice!nano v2 has a hardware defect: the VBUS detect line from USB-C to
the nRF52840 is broken. USB will never enumerate. All flashing must be done via
SWD using a Raspberry Pi 1 as a GPIO bitbang debugger.

## Hardware Setup

### SWD Wiring (RPi 1 → nice!nano v2)

| RPi 1 Physical Pin | GPIO   | nice!nano Pad | Signal |
|---------------------|--------|---------------|--------|
| 22                  | GPIO25 | Pin 6         | SWCLK  |
| 18                  | GPIO24 | Pin 7         | SWDIO  |

Power the nice!nano via a USB cable plugged into the RPi (shared GND).

The SWD pads on the nice!nano are flat — no soldering. You must hold the wires
firmly against the pads during the entire flashing process. If a command fails
with `Error connecting DP: cannot read IDR`, re-seat the wires and retry.

### RPi Access

```
ssh pi@192.168.1.108
password: Fredastaire1
```

Or from the PC:

```
sshpass -p 'Fredastaire1' ssh -o StrictHostKeyChecking=no pi@192.168.1.108
```

## Files on the RPi

All tools are at `/tmp/openocd-rpi/`:

```
/tmp/openocd-rpi/openocd       # statically linked ARM binary (cross-compiled)
/tmp/openocd-rpi/tcl/           # OpenOCD TCL scripts
/tmp/openocd-rpi/openocd.cfg    # OpenOCD config for RPi 1 + nRF52840
```

## Step-by-Step: Flashing New Firmware

### 1. Convert UF2 to Intel HEX

OpenOCD cannot flash .uf2 files. Convert them on the PC first:

```python
#!/usr/bin/env python3
"""Convert a UF2 file to Intel HEX for OpenOCD flashing."""
import struct
import sys

def uf2_to_hex(uf2_path, hex_path):
    blocks = []
    with open(uf2_path, 'rb') as f:
        while True:
            data = f.read(512)
            if not data or len(data) < 512:
                break
            magic0, magic1, flags, addr, size, block_no, num_blocks, family_id = \
                struct.unpack('<IIIIIIII', data[:32])
            if magic0 != 0x0A324655 or magic1 != 0x9E5D5157:
                continue
            blocks.append((addr, data[32:32+size]))

    blocks.sort(key=lambda x: x[0])
    print(f"  {len(blocks)} blocks, 0x{blocks[0][0]:08X} - 0x{blocks[-1][0]+len(blocks[-1][1]):08X}")

    with open(hex_path, 'w') as f:
        cur_ext = None
        for addr, payload in blocks:
            ext = (addr >> 16) & 0xFFFF
            if ext != cur_ext:
                cs = (256 - ((0x02 + 0x04 + (ext >> 8) + (ext & 0xFF)) & 0xFF)) & 0xFF
                f.write(f':02000004{ext:04X}{cs:02X}\n')
                cur_ext = ext
            offset = addr & 0xFFFF
            for i in range(0, len(payload), 16):
                chunk = payload[i:i+16]
                rec_addr = (offset + i) & 0xFFFF
                line = f'{len(chunk):02X}{rec_addr:04X}00'
                cs_val = len(chunk) + (rec_addr >> 8) + (rec_addr & 0xFF)
                for b in chunk:
                    line += f'{b:02X}'
                    cs_val += b
                f.write(f':{line}{(256 - (cs_val & 0xFF)) & 0xFF:02X}\n')
        f.write(':00000001FF\n')

if __name__ == '__main__':
    uf2_to_hex(sys.argv[1], sys.argv[2])
```

Usage:

```bash
python3 uf2_to_hex.py corne_right-nice_nano_v2-zmk.uf2 /tmp/corne_right.hex
python3 uf2_to_hex.py settings_reset-nice_nano_v2-zmk.uf2 /tmp/settings_reset.hex
```

### 2. Generate Bootloader Settings Page

**This is critical.** When flashing via SWD you bypass the bootloader, so it
doesn't know a valid app exists. Without this step, the bootloader stays in DFU
mode (blue LED blinking forever).

The Adafruit/nice!nano bootloader (SDK11-based) stores settings at a specific
flash address. Key discoveries:

- **nRF52840: settings address is `0xFF000`** (not `0xF3000`)
- The struct is `bootloader_settings_t` from Nordic SDK11 (not the SDK15+ `nrf_dfu_settings_t`)
- Fields are `uint16_t`, not `uint32_t`
- There is no CRC32 of the settings page itself
- Setting `bank_0_crc = 0` skips the app CRC check

```python
#!/usr/bin/env python3
"""Generate bootloader settings page for nice!nano v2 (nRF52840).

The settings page tells the Adafruit bootloader that a valid app exists.
Without it, the bootloader stays in DFU mode (blue LED blinking).

Usage: python3 gen_settings.py <app_hex> <output_hex>
"""
import struct
import sys

def get_app_size_from_hex(hex_path):
    """Read an Intel HEX file and return (start_addr, end_addr)."""
    min_addr, max_addr = float('inf'), 0
    ext_addr = 0
    with open(hex_path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith(':'):
                continue
            bc = int(line[1:3], 16)
            addr = int(line[3:7], 16)
            rt = int(line[7:9], 16)
            if rt == 4:
                ext_addr = int(line[9:13], 16) << 16
            elif rt == 0:
                full = ext_addr + addr
                min_addr = min(min_addr, full)
                max_addr = max(max_addr, full + bc)
    return min_addr, max_addr

def gen_settings(app_hex_path, output_hex_path):
    start, end = get_app_size_from_hex(app_hex_path)
    app_size = end - start
    print(f"  App: 0x{start:08X} - 0x{end:08X} ({app_size} bytes)")

    # bootloader_settings_t (SDK11, bootloader_types.h):
    #   uint16_t bank_0;       // 0x00 - BANK_VALID_APP = 0x01
    #   uint16_t bank_0_crc;   // 0x02 - 0 = skip CRC check
    #   uint16_t bank_1;       // 0x04 - BANK_INVALID_APP = 0xFF
    #   (2 bytes padding)      // 0x06
    #   uint32_t bank_0_size;  // 0x08
    #   uint32_t sd_image_size;  // 0x0C
    #   uint32_t bl_image_size;  // 0x10
    #   uint32_t app_image_size; // 0x14
    #   uint32_t sd_image_start; // 0x18
    page = bytearray([0xFF] * 4096)
    struct.pack_into('<H', page, 0x00, 0x0001)       # bank_0 = BANK_VALID_APP
    struct.pack_into('<H', page, 0x02, 0x0000)       # bank_0_crc = 0 (skip)
    struct.pack_into('<H', page, 0x04, 0x00FF)       # bank_1 = BANK_INVALID_APP
    struct.pack_into('<I', page, 0x08, app_size)     # bank_0_size
    struct.pack_into('<I', page, 0x0C, 0)            # sd_image_size
    struct.pack_into('<I', page, 0x10, 0)            # bl_image_size
    struct.pack_into('<I', page, 0x14, 0)            # app_image_size
    struct.pack_into('<I', page, 0x18, 0)            # sd_image_start

    # nRF52840: BOOTLOADER_SETTINGS_ADDRESS = 0xFF000
    settings_addr = 0xFF000
    write_len = 0x20  # 32 bytes covers the struct

    with open(output_hex_path, 'w') as f:
        ext = (settings_addr >> 16) & 0xFFFF
        cs = (256 - ((0x02 + 0x04 + (ext >> 8) + (ext & 0xFF)) & 0xFF)) & 0xFF
        f.write(f':02000004{ext:04X}{cs:02X}\n')
        offset = settings_addr & 0xFFFF
        for i in range(0, write_len, 16):
            chunk = page[i:i+16]
            rec_addr = (offset + i) & 0xFFFF
            line = f'{len(chunk):02X}{rec_addr:04X}00'
            cs_val = len(chunk) + (rec_addr >> 8) + (rec_addr & 0xFF)
            for b in chunk:
                line += f'{b:02X}'
                cs_val += b
            f.write(f':{line}{(256 - (cs_val & 0xFF)) & 0xFF:02X}\n')
        f.write(':00000001FF\n')

    print(f"  Settings written to {output_hex_path} at 0x{settings_addr:05X}")

if __name__ == '__main__':
    gen_settings(sys.argv[1], sys.argv[2])
```

Usage:

```bash
python3 gen_settings.py /tmp/corne_right.hex /tmp/bootloader_settings.hex
```

### 3. Copy Files to RPi

```bash
sshpass -p 'Fredastaire1' scp -o StrictHostKeyChecking=no \
    /tmp/corne_right.hex \
    /tmp/settings_reset.hex \
    /tmp/bootloader_settings.hex \
    pi@192.168.1.108:/tmp/openocd-rpi/
```

### 4. Flash via SWD

Connect the SWD wires and hold them firmly, then SSH into the RPi.

**Test connection first:**

```bash
cd /tmp/openocd-rpi
sudo ./openocd -s tcl -f openocd.cfg -c 'init; targets; shutdown'
```

You should see `SWD DPIDR 0x2ba01477`.

**Flash settings_reset (if doing a BLE re-pair):**

```bash
sudo ./openocd -s tcl -f openocd.cfg -c '
  init
  halt
  program settings_reset.hex verify
  reset
  shutdown'
```

**Flash the firmware:**

```bash
sudo ./openocd -s tcl -f openocd.cfg -c '
  init
  halt
  program corne_right.hex verify
  reset
  shutdown'
```

**Flash the bootloader settings (MANDATORY after every firmware flash via SWD):**

```bash
sudo ./openocd -s tcl -f openocd.cfg -c '
  init
  halt
  program bootloader_settings.hex verify
  reset
  shutdown'
```

After this last reset, the blue LED should turn off and the firmware should boot.

## Quick Reference: Full Update Procedure

When you have a new `corne_right...zmk.uf2` file:

```bash
# On PC: convert and generate settings
python3 uf2_to_hex.py corne_right_new.uf2 /tmp/corne_right.hex
python3 gen_settings.py /tmp/corne_right.hex /tmp/bootloader_settings.hex

# Copy to RPi
sshpass -p 'Fredastaire1' scp -o StrictHostKeyChecking=no \
    /tmp/corne_right.hex /tmp/bootloader_settings.hex \
    pi@192.168.1.108:/tmp/openocd-rpi/

# SSH into RPi and flash (hold SWD wires firmly)
sshpass -p 'Fredastaire1' ssh pi@192.168.1.108
cd /tmp/openocd-rpi
sudo ./openocd -s tcl -f openocd.cfg -c '
  init; halt
  program corne_right.hex verify
  program bootloader_settings.hex verify
  reset; shutdown'
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Error connecting DP: cannot read IDR` | SWD wires lost contact | Re-seat wires, hold firmly, retry |
| Blue LED blinking after flash | Bootloader settings missing or at wrong address | Flash `bootloader_settings.hex` at `0xFF000` |
| Blue LED blinking (old settings format) | Used SDK15+ struct instead of SDK11 | Regenerate with `gen_settings.py` (uses `uint16_t` fields) |
| `APPROTECT` enabled, can't connect | Chip is locked | Run `nrf52_recover` first (wipes everything) |
| Firmware flashed but no BLE pairing | Need settings_reset on both halves | Flash `settings_reset.hex` on right, drag `.uf2` on left |

## Key Technical Details

- **Chip**: nRF52840-QI/CAAA(D0), 1024kB Flash, 256kB RAM, SWD DPIDR `0x2ba01477`
- **Bootloader**: Adafruit nRF52 Bootloader 0.10.0 (SDK11-based, at `0xF4000`)
- **SoftDevice**: S140 (included in the bootloader hex, occupies `0x1000`-`0x26000`)
- **App region**: starts at `0x26000`
- **Bootloader settings address (nRF52840)**: `0xFF000`
- **MBR params page**: `0xFE000`
- **Settings struct**: `bootloader_settings_t` from SDK11 with `uint16_t` fields (NOT the SDK15+ `nrf_dfu_settings_t` with `uint32_t` fields and CRC32)
- **RPi 1 peripheral_base**: `0x20000000` (vs `0x3F000000` for RPi 2/3)
- **OpenOCD**: must be compiled with `--enable-bcm2835gpio`, statically linked for ARM to avoid glibc mismatch

## Flash Layout (nRF52840)

```
 0x00000  ┌─────────────────────────┐
          │ MBR (4 KB)              │
 0x01000  ├─────────────────────────┤
          │ SoftDevice S140         │
          │ (~148 KB)               │
 0x26000  ├─────────────────────────┤
          │ Application (ZMK)       │
          │ (~300 KB for right)     │
          │                         │
 0xF4000  ├─────────────────────────┤
          │ Bootloader (38 KB)      │
 0xFD800  ├─────────────────────────┤
          │ Bootloader Config (2KB) │
 0xFE000  ├─────────────────────────┤
          │ MBR Params Page (4 KB)  │
 0xFF000  ├─────────────────────────┤
          │ Bootloader Settings     │  <-- THIS IS WHERE THE MAGIC HAPPENS
          │ (4 KB)                  │
 0x100000 └─────────────────────────┘
```
