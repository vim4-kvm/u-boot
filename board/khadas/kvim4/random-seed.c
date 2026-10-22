// SPDX-License-Identifier: GPL-2.0+
#include <common.h>
#include <asm/io.h>
#include <asm/arch/secure_apb.h>
#include <linux/errno.h>
#include <linux/libfdt.h>
#include "random-seed.h"

/* Bound each handshake so an unavailable RNG cannot prevent booting. */
static int vim4_rng_wait(u32 mask)
{
	unsigned int i;

	for (i = 0; i < 1000; i++) {
		if (!(readl(RNG_REE_CFG) & mask))
			return 0;
		udelay(10);
	}
	return -ETIMEDOUT;
}

static int vim4_rng_read(u32 *word)
{
	int ret;

	/* T7 uses the version-2 CFG/OUT0 handshake, also used by Linux. */
	writel(readl(RNG_REE_CFG) | BIT(31), RNG_REE_CFG);
	ret = vim4_rng_wait(BIT(31));
	if (ret)
		return ret;
	ret = vim4_rng_wait(BIT(0));
	if (ret)
		return ret;
	*word = readl(RNG_REE_OUT0);
	return 0;
}

int vim4_random_seed_fixup(void *blob)
{
	u32 words[10];
	fdt64_t kaslr;
	int chosen, i, ret;

	chosen = fdt_path_offset(blob, "/chosen");
	if (chosen < 0)
		return chosen;
	/* A retried boot must never retain a seed from a previous attempt. */
	fdt_delprop(blob, chosen, "kaslr-seed");
	fdt_delprop(blob, chosen, "rng-seed");
	for (i = 0; i < ARRAY_SIZE(words); i++) {
		ret = vim4_rng_read(&words[i]);
		if (ret)
			goto out;
		/* Reject obvious stuck output; this is not an entropy estimate. */
		if (!words[i] || words[i] == ~0U ||
		    (i && words[i] == words[i - 1])) {
			ret = -EIO;
			goto out;
		}
	}
	/* Independent samples for address randomization and the entropy pool. */
	kaslr = cpu_to_fdt64(((u64)words[0] << 32) | words[1]);
	ret = fdt_setprop(blob, chosen, "kaslr-seed", &kaslr, sizeof(kaslr));
	if (ret)
		goto out;
	ret = fdt_setprop(blob, chosen, "rng-seed", &words[2], 32);
	if (ret)
		fdt_delprop(blob, chosen, "kaslr-seed");
out:
	memset(words, 0, sizeof(words));
	return ret;
}
