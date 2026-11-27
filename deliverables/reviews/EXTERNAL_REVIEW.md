# External local-process review

Passed expectations: **11/11**

Each case was staged from a published ZIP into a fresh temporary directory and
verified by a new Python process using only files inside that staging directory.

| Case | Exit | Expected | Result | Break / warning |
|---|---:|---:|---|---|
| 01-qixia-crosslibrary-happy | 0 | 0 | PASS EXPECTATION |  |
| 01b-qixia-same-book-registry-closed | 0 | 0 | PASS EXPECTATION | registry-online-unavailable |
| 02-online-http-complete | 0 | 0 | PASS EXPECTATION |  |
| 03-dengying-v1-tui-happy | 0 | 0 | PASS EXPECTATION |  |
| 04-dengying-last-renewal-late | 2 | 2 | PASS EXPECTATION | renewal-4 |
| 04b-dengying-date-edit-without-resign | 2 | 2 | PASS EXPECTATION | renewal-4 |
| 05-dengying-swap-reader-to-v2-qiao | 2 | 2 | PASS EXPECTATION | reader-original |
| 06-qixia-owner-one-day-late | 2 | 2 | PASS EXPECTATION | migration-2026 |
| 07-qixia-stamp-after-trust-end | 2 | 2 | PASS EXPECTATION | migration-2026 |
| 08-registry-closed-history-still-verifies | 0 | 0 | PASS EXPECTATION | registry-online-unavailable |
| 09-registry-today-statement-only | 2 | 2 | PASS EXPECTATION | registry-current-statement |

## Verifier summaries

### 01-qixia-crosslibrary-happy

- expectation: passed
- summary: VERIFIED: exact reader bytes match the old receipt; original-time checks and a timely successor-witness bridge validate the complete witness history offline.

### 01b-qixia-same-book-registry-closed

- expectation: passed
- summary: VERIFIED: exact reader bytes match the old receipt; original-time checks and a timely successor-witness bridge validate the complete witness history offline.

### 02-online-http-complete

- expectation: passed
- summary: VERIFIED: exact reader bytes match the old receipt; original-time checks and a timely successor-witness bridge validate the complete witness history offline.

### 03-dengying-v1-tui-happy

- expectation: passed
- summary: VERIFIED: signatures, digest bindings, sequence, and renewal links are intact.

### 04-dengying-last-renewal-late

- expectation: passed
- summary: NOT VERIFIED (1 failure(s)); first break is renewal-4.

### 04b-dengying-date-edit-without-resign

- expectation: passed
- summary: NOT VERIFIED (2 failure(s)); first break is renewal-4.

### 05-dengying-swap-reader-to-v2-qiao

- expectation: passed
- summary: NOT VERIFIED (8 failure(s)); first break is reader-original.

### 06-qixia-owner-one-day-late

- expectation: passed
- summary: NOT VERIFIED (1 failure(s)); first break is migration-2026.

### 07-qixia-stamp-after-trust-end

- expectation: passed
- summary: NOT VERIFIED (1 failure(s)); first break is migration-2026.

### 08-registry-closed-history-still-verifies

- expectation: passed
- summary: VERIFIED: signatures, digest bindings, sequence, and renewal links are intact.

### 09-registry-today-statement-only

- expectation: passed
- summary: NOT VERIFIED: registry's current statement alone cannot re-prove prior witnessing; original point-in-time credentials are required.
