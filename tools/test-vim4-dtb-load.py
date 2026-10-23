#!/usr/bin/env python3
"""Exercise VIM4 Linux DTB selection and reject the raw reserved-DTB fallback."""
import ast
import os
from pathlib import Path
import re
import subprocess
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    header = (root / 'board/khadas/configs/kvim4.h').read_text()
    section = header.split('#ifdef CONFIG_DTB_BIND_KERNEL', 1)[1].split('#endif', 1)[0]
    linux = section.split('#else', 1)[1]
    command = ''.join(ast.literal_eval(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', linux))
    # Bash and U-Boot hush share the if/test/load syntax in this command.
    # A raw fallback stands in for the observed non-returning hardware path.
    fixture = r'''
load() {
    test "$*" = "mmc $expected 0x01000000 dtb/amlogic/kvim4.dtb" || exit 98
    echo FILE_LOAD
    return "$load_status"
}
imgread() { echo UNSAFE_RESERVED_DTB >&2; exit 99; }
'''
    for source, device in [('emmc', '1:1'), ('sd', '0:1')]:
        for status in (0, 1):
            env = dict(os.environ, boot_source=source, expected=device,
                       dtb_mem_addr='0x01000000', fdtfile='amlogic/kvim4.dtb',
                       load_status=str(status))
            result = subprocess.run(['bash', '-c', fixture + command], env=env,
                                    text=True, capture_output=True)
            assert result.returncode == status, result
            assert result.stdout.count('FILE_LOAD') == 1, result
    result = subprocess.run(['bash', '-c', fixture + command],
                            env=dict(os.environ, boot_source='unknown'), capture_output=True)
    assert result.returncode != 0 and b'FILE_LOAD' not in result.stdout

    driver = (root / 'cmd/amlogic/imgread.c').read_text()
    start = driver.index('static int do_image_read_dtb_from_rsv(')
    function = driver[start:driver.index('\n}\n', start) + 3]
    source = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#define CONFIG_TARGET_KHADAS_VIM4 1
#define CONFIG_SKIP_KERNEL_DTB_SECBOOT_CHECK 1
#define errorP(...) printf(__VA_ARGS__)
/* A bus hang/reset cannot be recovered with a software poll timeout.
 * The Linux entry and the common late-init fallback must not reach it.
 */
#define store_rsv_size(...) (abort(), 0)
#define store_rsv_read(...) (abort(), 0)
static bool android_img __attribute__((unused));
'''
    source += function + r'''
int main(void) {
    assert(do_image_read_dtb_from_rsv(NULL) == -EOPNOTSUPP);
    puts("PASS: Linux filesystem DTB selection, failed loads, no raw reserved-DTB access");
}
'''
    with tempfile.TemporaryDirectory(prefix='vim4-dtb-test-') as temporary:
        test = Path(temporary) / 'test.c'
        executable = Path(temporary) / 'test'
        test.write_text(source)
        subprocess.run([os.environ.get('HOSTCC', 'cc'), '-Wall', '-Wextra',
                        '-Wno-unused-parameter', str(test), '-o', str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == '__main__':
    main()
