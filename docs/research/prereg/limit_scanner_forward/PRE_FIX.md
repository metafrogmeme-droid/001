# Label: this forward test is pre-fix

Added 2026-11-12 (UTC). The other files in this directory are kept exactly
as they were written; `tests/test_the_reclaim_forward_test_is_its_own_test.py`
pins their bytes.

This test was registered on 2026-10-25 under the reclaim fill that read its
own bar's close and booked the entry at the limit price. PR #773 fixed that
fill on 2026-11-11, and the code no longer runs V1 and V3 as they were
registered here. V0 and V2 fill as they did.

By the owner's decision of 2026-11-12, V1 and V3 as they now fill are a new
test with their own cutoff and N: `docs/research/prereg/limit_scanner_reclaim_forward/`.
Any verdict this directory's test reaches for V1 or V3 is a verdict on the
pre-fix definition, which can no longer be reproduced from this code.
