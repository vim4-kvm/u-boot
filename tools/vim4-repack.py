#!/usr/bin/env python3
"""Replace BL33 in the known Fenix 1.7.5 VIM4 SD/eMMC boot image, offline.

Preserve the vendor boot stages and BL30; rebuild the device FIP using the
vendor tool. This does not write any device. See VIM4.md for the build steps.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess


STOCK_SHA256 = "95547c4cd3624fab11ff118668bd15d1497aef8c8339049c714566bb63adadee"
FIP_OFFSET = 0xA4200
PARTS = ("bl30", "bl40", "bl31", "bl32", "bl33")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stock", type=Path)
    parser.add_argument("output", type=Path, help="new, empty output directory")
    parser.add_argument("--update-bl31", action="store_true",
                        help="replace BL31 with the signed blob in this source tree")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    stock = args.stock.read_bytes()
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
        parser.error("unsupported stock image: expected Fenix 1.7.5 VIM4 SD image")
    args.output.mkdir(parents=True, exist_ok=False)
    out = args.output.resolve()
    original = stock[FIP_OFFSET:]
    entries = [struct.unpack_from("<QQ", original, 48 + 40 * i) for i in range(5)]

    command = [str(root / "fip/t7/binary-tool/acpu-imagetool"), "create-device-fip",
               f"--infile-template-chipset-fip-header={root}/soc/templates/t7/a311d2/device-fip-header.bin"]
    for part, index, size in (("bl30", 0, 65536), ("bl33", 4, 1572864)):
        offset, _ = entries[index]
        # Vendor blobs contain a 16-byte nonce before the fixed-size payload.
        (out / f"{part}-payload.bin").write_bytes(original[offset + 16:offset + 16 + size])
        (out / f"{part}-nonce.bin").write_bytes(original[offset:offset + 16])
        command += [f"--infile-{part}-payload={out}/{part}-payload.bin",
                    f"--infile-nonce-blob-{part}={out}/{part}-nonce.bin"]
    blobs = {}
    for part, folder in (("bl40", "bl40/bin"), ("bl31", "bl31/bl31_1.3/bin"),
                         ("bl32", "bl32/bl32_3.8/bin")):
        blob = (root / folder / 't7/a311d2' / f'blob-{part}.bin.signed').read_bytes()
        offset, size = entries[PARTS.index(part)]
        if len(blob) < size or any(blob[size:]):
            raise RuntimeError(f'{part} signed blob size or padding changed')
        if part != 'bl31' or not args.update_bl31:
            if blob[:size] != original[offset:offset + size]:
                raise RuntimeError(f'{part} differs from stock without an allowed update')
        blobs[part] = blob
        # First round-trip only stock data, including stock BL31.
        path = out / f'blob-{part}.bin.signed'
        path.write_bytes(original[offset:offset + size].ljust(len(blob), b'\0'))
        command.append(f"--infile-blob-{part}={path}")

    def pack(name):
        target = out / name
        with (out / f"{name}.log").open("w") as log:
            subprocess.run(command + [f"--outfile-device-fip={target}"],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        data = target.read_bytes()
        if len(data) != len(original) or data[16:480] != original[16:480]:
            raise RuntimeError("device FIP layout changed")
        return data

    # Exercise the complete packaging path with the original payload first.
    roundtrip = pack("roundtrip.bin")
    if roundtrip[0x8000:] != original[0x8000:]:
        raise RuntimeError("stock round-trip changed a firmware blob")
    if args.update_bl31:
        (out / 'blob-bl31.bin.signed').write_bytes(blobs['bl31'])

    uboot = (root / "build/u-boot.bin").read_bytes()
    stub = (root / "bl33z/build/bl33z.bin").read_bytes()
    aligned = uboot + bytes((-len(uboot)) % 4096) + stub
    (out / "bl33-with-ramdump.bin").write_bytes(aligned)
    subprocess.run([str(root / "fip/t7/aml_encrypt_t7"), "--bl3sig", "--input",
                    str(out / "bl33-with-ramdump.bin"), "--output", str(out / "bl33.lz4"),
                    "--compress", "lz4", "--level", "v3", "--type", "bl33"], check=True)
    # Same wrapper removal and fixed payload size as fip/t7/build.sh.
    compressed = (out / "bl33.lz4").read_bytes()[1824:]
    if not compressed or len(compressed) > 1572864:
        raise RuntimeError("compressed BL33 does not fit")
    payload = compressed.ljust(1572864, b"\0")
    (out / "bl33-payload.bin").write_bytes(payload)
    updated = pack("device-fip.bin")
    for part, (offset, size) in zip(PARTS[:4], entries[:4]):
        expected = blobs['bl31'][:size] if part == 'bl31' and args.update_bl31 else original[offset:offset + size]
        if updated[offset:offset + size] != expected:
            raise RuntimeError(f"{part} payload verification failed")
    offset, _ = entries[4]
    if updated[offset + 16:offset + 16 + len(payload)] != payload:
        raise RuntimeError("BL33 payload verification failed")
    image = stock[:FIP_OFFSET] + updated
    target = out / "u-boot.bin.sd.bin.signed"
    target.write_bytes(image)
    report = {'stock_sha256': STOCK_SHA256,
              'image_sha256': hashlib.sha256(image).hexdigest(),
              'update_bl31': args.update_bl31,
              'regions': {part: hashlib.sha256(updated[o:o+s]).hexdigest()
                          for part, (o, s) in zip(PARTS, entries)},
              'validation': 'Offline round-trip and payload checks only; not boot-tested'}
    (out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"{hashlib.sha256(image).hexdigest()}  {target}")
    print("Verified stock round-trip, preserved early stages/BL30/BL40/BL32 and expected BL31/BL33")


if __name__ == "__main__":
    main()
