#!/usr/bin/env python3
"""Host regression for the actual FUSB302 observer and FDT encoder; no hardware.

Compile selected driver functions unchanged with mocked controller I/O and this
tree's libfdt. Selection and Request bytes are deliberately tested as they exist,
including the vendor difference between policy current and RDO operating current.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void|enum tx_state) ' + name + r'\(', source, re.M)
    if not match:
        raise ValueError('Missing driver function: ' + name)
    end = source.index('\n}\n', match.start()) + 3
    return source[match.start():end]


def main():
    root = Path(__file__).resolve().parents[1]
    driver = root.joinpath('drivers/power/mfd/fusb302.c').read_text()
    constants = driver[driver.index('#define FUSB30X_I2C_DEVICETREE_NAME'):driver.index('static struct fusb30x_chip chip;')]
    observer = driver[driver.index('#define PD_BOOT_COMPLETE'):driver.index('static int fusb302_gpio_is_valid')]
    names = ('set_state', 'set_mesg', 'policy_send_data', 'fusb_set_pos_power',
             'fusb302_set_pos_power_by_charge_ic', 'fusb_state_snk_evaluate_caps',
             'fusb_state_snk_select_cap', 'fusb_state_snk_transition_sink', 'tcpc_alert')
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <libfdt.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define debug(...) do {} while (0)
#include "power/fusb302.h"
static u8 registers[256], sent[40];
static int writes, reads, resets;
static int fusb302_i2c_read(u8 reg, u8 *value) { reads++; *value = registers[reg]; return 0; }
static int fusb302_i2c_update_bits(u8 reg, u8 mask, u8 val) { writes++; return 0; }
static int fusb302_i2c_raw_write(u8 reg, const void *value, size_t len)
{ writes++; memcpy(sent, value, len); return 0; }
static void fusb302_flush_rx_fifo(struct fusb30x_chip *chip) {}
static void fusb302_pd_reset(struct fusb30x_chip *chip) { resets++; }
'''
    source += constants + observer + '\n'.join(function(driver, name) for name in names)
    source += r'''
int main(void)
{
    struct fusb30x_chip chip = {0};
    char blobs[2][4096];
    int i, len, chosen, before_reads, before_writes, evt = 0;
    const fdt32_t *cells;
    boot_pd.flags = PD_BOOT_SINK_ATTACHED;
    boot_pd.rp = 3;
    chip.rec_head = 0x2061;
    chip.rec_load[0] = 0x3e01912c; /* 5 V, 3 A */
    chip.rec_load[1] = (400 << 10) | 300; /* 20 V exceeds unchanged policy */
    fusb_state_snk_evaluate_caps(&chip, EVENT_RX);
    assert(chip.pos_power == 1 && chip.pd_output_vol == 5000 && chip.pd_output_cur == 3000);
    fusb_state_snk_select_cap(&chip, 0);
    assert(writes == 1 && chip.tx_state == tx_busy);
    assert(boot_pd.rdo == 0x1401912c); /* unchanged vendor Request */
    assert(((boot_pd.rdo >> 10) & 1023) == 100); /* operating 1 A */
    assert(boot_pd.policy_ma == 3000);
    assert(!(boot_pd.flags & PD_BOOT_COMPLETE));
    chip.tx_state = tx_success;
    chip.rec_head = CMT_ACCEPT;
    fusb_state_snk_select_cap(&chip, EVENT_RX);
    assert(chip.conn_state == policy_snk_transition_sink);
    assert(!(boot_pd.flags & PD_BOOT_COMPLETE));
    chip.rec_load[0] = 0xdeadbeef; /* receive buffer is reused for control messages */
    chip.rec_head = CMT_PS_RDY | (1 << 12);
    fusb_state_snk_transition_sink(&chip, EVENT_RX);
    assert(!(boot_pd.flags & PD_BOOT_COMPLETE));
    chip.rec_head = CMT_PS_RDY;
    fusb_state_snk_transition_sink(&chip, EVENT_RX);
    assert(boot_pd.flags & PD_BOOT_COMPLETE);
    assert(chip.conn_state == policy_snk_ready);
    assert(boot_pd.pdo[0] == 0x3e01912c);
    for (i = 0; i < 2; i++) {
        assert(fdt_create_empty_tree(blobs[i], sizeof(blobs[i])) == 0);
        if (i == 0)
            assert(fdt_setprop_string(blobs[i], 0, "amlogic-dt-id", "kvim4n") == 0);
        chosen = fdt_add_subnode(blobs[i], 0, "chosen");
        assert(chosen >= 0);
        assert(fdt_setprop_string(blobs[i], chosen, "bootargs", "test") == 0);
        before_reads = reads;
        before_writes = writes;
        assert(fusb302_fixup_fdt(blobs[i]) == 0);
        assert(reads == before_reads && writes == before_writes);
        assert(fdt_pack(blobs[i]) == 0);
        chosen = fdt_path_offset(blobs[i], "/chosen");
        cells = fdt_getprop(blobs[i], chosen, "khadas,usb-pd-boot", &len);
        assert(cells && len == 64);
        assert(fdt32_to_cpu(cells[0]) == 1 && fdt32_to_cpu(cells[1]) == 65);
        assert(fdt32_to_cpu(cells[4]) == 0x1401912c);
        assert(fdt32_to_cpu(cells[9]) == 0x3e01912c);
        /* Replacing the existing snapshot also works at packed size. */
        assert(fusb302_fixup_fdt(blobs[i]) == 0);
    }
    registers[FUSB_REG_INTERRUPTA] = INTERRUPTA_HARDRST;
    tcpc_alert(&chip, &evt);
    assert(resets == 1 && (evt & EVENT_REC_RESET));
    assert(!(boot_pd.flags & PD_BOOT_COMPLETE));
    assert(boot_pd.flags & PD_BOOT_HARD_RESET_RX);
    chip.rec_head = CMT_PS_RDY;
    fusb_state_snk_transition_sink(&chip, EVENT_RX);
    assert(!(boot_pd.flags & PD_BOOT_COMPLETE)); /* no Request after reset */
    fusb302_report_boot_pd();
    assert(boot_pd.flags & PD_BOOT_TYPEC_FALLBACK);
    assert(fusb302_fixup_fdt(blobs[0]) == 0);
    chosen = fdt_path_offset(blobs[0], "/chosen");
    cells = fdt_getprop(blobs[0], chosen, "khadas,usb-pd-boot", &len);
    assert(fdt32_to_cpu(cells[1]) & PD_BOOT_TYPEC_FALLBACK);
    assert(!(fdt32_to_cpu(cells[1]) & PD_BOOT_COMPLETE));
    /* A missing /chosen or insufficient space fails without controller I/O. */
    assert(fdt_create_empty_tree(blobs[0], sizeof(blobs[0])) == 0);
    assert(fusb302_fixup_fdt(blobs[0]) == -FDT_ERR_NOTFOUND);
    assert(fdt_add_subnode(blobs[0], 0, "chosen") >= 0);
    assert(fdt_pack(blobs[0]) == 0);
    assert(fusb302_fixup_fdt(blobs[0]) == -FDT_ERR_NOSPACE);
    puts("PASS: unchanged Request, PS_RDY gate, raw PDO retention, hard reset, vendor/mainline FDT, no fixup I/O");
    return 0;
}
'''
    libfdt = root / 'scripts/dtc/libfdt'
    with tempfile.TemporaryDirectory(prefix='vim4-pd-test-') as directory:
        test = Path(directory) / 'test.c'
        test.write_text(source)
        executable = Path(directory) / 'test'
        # The firmware builds lib/libfdt/fdt_ro.c instead of the older dtc copy.
        # Keep that implementation, adapting only its U-Boot include preamble.
        read_only = root.joinpath('lib/libfdt/fdt_ro.c').read_text()
        preamble = read_only.index('#include "libfdt_internal.h"')
        host_ro = Path(directory) / 'fdt_ro.c'
        host_ro.write_text('#include <libfdt_env.h>\n#include <libfdt.h>\n' + read_only[preamble:])
        subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-Wno-unused-parameter', '-Wno-sign-compare', '-Wno-implicit-fallthrough',
                        '-Wno-overflow', '-I' + str(libfdt), '-iquote', str(root / 'include'),
                        str(test), str(host_ro), *[str(libfdt / (name + '.c')) for name in
                                     ('fdt', 'fdt_rw', 'fdt_wip', 'fdt_sw', 'fdt_empty_tree')],
                        '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    main()
