# VIM4 quiet boot experiment

Based on Khadas release `khadas-vims-u-boot-2019.01-v1.7.5-release`
(`9834e1b545e3a00ac8d2d49821244acc5b03b3a4`). Branch: `vim4-kvm-2019.01`.
`board_init()` no longer sends the MCU fan-test command (I2C 6, address
0x18, register 0x96, value 1). Linux thermal control remains responsible
for the fan after boot.

## Build and package

Use a case-sensitive Linux filesystem. Tested build host: Ubuntu 24.04
ARM64 in OrbStack, GCC 12, binutils 2.42; OrbStack executes the vendor's
x86-64 packaging binaries. Dependencies include make, gcc-12, binutils,
bison, flex, libssl-dev, and Python 3.

```sh
make kvim4_defconfig
CONFIG_SUPPORT_BL33Z=1 make -j10 CROSS_COMPILE=aarch64-linux-gnu- CC=gcc-12 \
  KCFLAGS='-Wno-error=array-bounds -Wno-error=misleading-indentation' \
  LDFLAGS=--no-warn-rwx-segments
make -C bl33z PLAT=kvim4 AARCH=aarch64 CROSS_COMPILE=aarch64-linux-gnu- \
  CC=gcc-12 LDFLAGS='--no-warn-rwx-segments --gc-sections'
python3 tools/vim4-repack.py /path/to/stock-u-boot.bin.sd.bin.signed build/quiet-boot
```

The warning exceptions accommodate existing vendor code with modern tools.
The stock input is `/usr/lib/u-boot/u-boot.bin.sd.bin.signed` from package
`linux-u-boot-vim4-vendor 1.7.5-2019.01`. Its SHA256 is pinned in the script.
The script preserves the stock early boot stages, DDR firmware and BL30,
verifies BL31/32/40 against this source tree, and first round-trips the stock
payload through the vendor packer. Only BL33 and its device FIP metadata are
replaced. It appends the ramdump stub and compresses BL33 using the vendor
format. The output is for the generic SD/eMMC layout, not SPI or Android
vendor partitions. The script never writes a block device.

Before installation, back up the eMMC prefix through the first partition,
check the image fits before that partition, and preserve bytes 442–511 of
sector zero (disk identity and partition table), as the vendor installer does.
Read back and compare all written bytes before rebooting. Retain the SPI
OOWOW recovery environment. Runtime fan `speed` is a requested percentage,
not a tachometer reading; boot logs alone cannot verify the acoustic effect.

## USB-PD boot handoff candidate

The versioned `/chosen/khadas,usb-pd-boot` snapshot and its limits are described
in [doc/README.usb-pd-boot](doc/README.usb-pd-boot). Run
`python3 tools/test-vim4-pd-handoff.py` for host-only event/FDT regression.
Use a separate worktree and `make O=/absolute/output` for BL33. The repacker
expects `build/u-boot.bin` and `bl33z/build/bl33z.bin` in that worktree; these
directories can be symlinks to its private BL33 output and BL33Z `BUILD_BASE`.
The edge baseline already uses the updated BL31, so repacking from the pinned
stock input requires `--update-bl31` and comparison with the installed edge
image to prove BL31 is unchanged. This candidate has not been flashed: existing
records rely on SPI OOWOW if Linux cannot boot and do not prove a tested
U-Boot rollback independent of recovery. Do not flash under a no-OOWOW policy.
