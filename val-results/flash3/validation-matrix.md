# Validation Matrix Results

| Family | Board | Tier | Verdict | Pass Type | Chip | Evidence SHA | Retries |
| ------ | ----- | ---- | ------- | --------- | ---- | ------------ | ------- |
| flash3 | leonardo | 3 | PASS | authoritative | SST39SF040 | c19c3e07b94b12be… | 1 |
| flash3 | uno328pb | 3 | N/A | — | — | — | 0 |

**Notes:**
- Leonardo cell: Bonus bench run 2026-06-17. SST39SF040 used (planned AM29F040 absent). configure_flash3 erase+write PASS on Leonardo Rev 2.0. Negative control: verify against wrong file exited 1 (0x7e != 0xa3 at 0x000000).
