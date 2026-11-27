# Mutation sequence review

Overall: **PASS**

All operations ran in a fresh temporary lab using published ZIPs/reader files only.

| Step | Result | Exit | Break | Key failure | Summary |
|---|---|---:|---|---|---|
| 1-verify-v1-before-mutation | PASS | 0 |  |  | VERIFIED: signatures, digest bindings, sequence, and renewal links are intact. |
| 2a-move-date-only-without-resign | PASS | 2 | renewal-4 | signature-invalid, deadline-anchor-late | NOT VERIFIED (2 failure(s)); first break is renewal-4. |
| 2b-honest-late-reissuance-after-deadline | PASS | 2 | renewal-4 | deadline-anchor-late | NOT VERIFIED (1 failure(s)); first break is renewal-4. |
| 3-restore-date-and-reverify-v1 | PASS | 0 |  |  | VERIFIED: signatures, digest bindings, sequence, and renewal links are intact. |
| 4-swap-only-reader-original-to-v2 | PASS | 2 | reader-original | digest-mismatch, digest-mismatch, digest-mismatch, digest-mismatch, digest-mismatch, digest-mismatch, digest-mismatch | NOT VERIFIED (8 failure(s)); first break is reader-original. |
