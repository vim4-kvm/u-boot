#!/usr/bin/env python3
"""Run the actual seed handoff with bounded MMIO faults and real libfdt."""
from pathlib import Path
import os
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    driver = (root / 'board/khadas/kvim4/random-seed.c').read_text()
    source = r'''
#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <libfdt.h>
typedef uint32_t u32;
typedef uint64_t u64;
#define BIT(n) (1U << (n))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define RNG_REE_CFG 0
#define RNG_REE_OUT0 8
static u32 input[10], stuck;
static int sample, fault_at, delays;
static u32 readl(int address) {
    if (address == RNG_REE_OUT0) return input[sample];
    return sample == fault_at ? stuck : 0;
}
static void writel(u32 value, int address) {
    assert(address == RNG_REE_CFG && (value & BIT(31)));
    sample++;
}
static void udelay(int us) { assert(us == 10); delays++; }
static void reset(void) {
    sample=-1; fault_at=-2; stuck=0; delays=0;
    for (int i=0;i<10;i++) input[i]=0x12345670+i;
}
'''
    source += driver[driver.index('static int vim4_rng_wait'):]
    source += r'''
static int tree(char *blob, int size) {
    assert(fdt_create_empty_tree(blob,size)==0);
    int node=fdt_add_subnode(blob,0,"chosen"); assert(node>=0); return node;
}
static void absent(char *blob) {
    int node=fdt_path_offset(blob,"/chosen");
    assert(!fdt_getprop(blob,node,"kaslr-seed",NULL));
    assert(!fdt_getprop(blob,node,"rng-seed",NULL));
}
int main(void) {
    char blob[4096]; int node,len,ret;
    reset(); node=tree(blob,sizeof(blob));
    assert(vim4_random_seed_fixup(blob)==0);
    const fdt64_t *seed=fdt_getprop(blob,node,"kaslr-seed",&len);
    assert(seed && len==8 && fdt64_to_cpu(*seed)==0x1234567012345671ULL);
    const void *rng=fdt_getprop(blob,node,"rng-seed",&len);
    assert(rng && len==32 && !memcmp(rng,&input[2],32));
    /* Retried boot replaces both old properties, including a malformed seed. */
    assert(fdt_setprop_string(blob,node,"kaslr-seed","stale")==0);
    reset(); input[0]=0x76543210;
    assert(vim4_random_seed_fixup(blob)==0);
    seed=fdt_getprop(blob,node,"kaslr-seed",&len);
    assert(len==8 && fdt64_to_cpu(*seed)==0x7654321012345671ULL);
    /* Each timeout removes old seeds and stops before reading stale OUT0. */
    for (int bit=0;bit<=31;bit+=31) {
        reset(); fault_at=3; stuck=BIT(bit);
        assert(vim4_random_seed_fixup(blob)==-ETIMEDOUT);
        assert(delays==1000 && sample==3); absent(blob);
    }
    for (int mode=0;mode<3;mode++) {
        reset(); input[4]=mode==0?0:mode==1?~0U:input[3];
        assert(vim4_random_seed_fixup(blob)==-EIO); absent(blob);
    }
    /* Test every FDT capacity around the two-property allocation boundary. */
    for (int size=128;size<400;size++) {
        reset(); tree(blob,sizeof(blob)); assert(fdt_pack(blob)==0);
        assert(fdt_open_into(blob,blob,size)==0);
        ret=vim4_random_seed_fixup(blob);
        if (ret) { assert(ret==-FDT_ERR_NOSPACE); absent(blob); }
    }
    reset(); assert(fdt_create_empty_tree(blob,sizeof(blob))==0);
    assert(vim4_random_seed_fixup(blob)==-FDT_ERR_NOTFOUND && sample==-1);
    puts("PASS: fresh seeds, FDT encoding, retry, bounded RNG faults and allocation rollback");
}
'''
    libfdt = root / 'scripts/dtc/libfdt'
    with tempfile.TemporaryDirectory(prefix='vim4-seed-test-') as directory:
        directory = Path(directory)
        test = directory / 'test.c'
        test.write_text(source)
        read_only = (root / 'lib/libfdt/fdt_ro.c').read_text()
        host_ro = directory / 'fdt_ro.c'
        host_ro.write_text('#include <libfdt_env.h>\n#include <libfdt.h>\n' +
                           read_only[read_only.index('#include "libfdt_internal.h"'):])
        executable = directory / 'test'
        subprocess.run([os.environ.get('HOSTCC', 'cc'), '-std=gnu11', '-Wall', '-Wextra',
                        '-Wno-sign-compare', '-Wno-implicit-fallthrough',
                        '-I' + str(libfdt), str(test), str(host_ro),
                        *[str(libfdt / (name + '.c')) for name in
                          ('fdt', 'fdt_rw', 'fdt_wip', 'fdt_sw', 'fdt_empty_tree')],
                        '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    main()
