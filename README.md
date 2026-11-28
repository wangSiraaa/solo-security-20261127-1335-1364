# 跨馆引荐与接件演练 (Museum Referral & Acceptance Drill)

An offline, reproducible reference implementation of a cross-museum referral
and acceptance workflow.  It answers the opening question —

> 两条跨馆引荐都是真的，馆员究竟能以哪个名字签这件藏品？
> *(Both cross-museum referrals are genuine — under which name may the
> librarian sign this item?)*

— with a checkable answer: **the name that comes from the same single referral
path that also supplies the scope, purpose and validity for that exact item.**
No borrowing the name from one path and the signature authority from another.

Nothing here talks to a network or a real identity service.  Every key is a
local "drill" key; every credential and document is plain JSON that can be
handed over offline and re-checked years later by a successor.

## Quick start

```bash
python3 -m museum.demo                              # full narrated scenario
python3 -m unittest discover -s tests -p 'test_*.py' # 37 tests
```

Requires only Python 3.10+ and the system `openssl` CLI (Ed25519).  There are
no pip dependencies.

## Design in one page

**Identity = key fingerprint, never the short name.**  Several museums may
register the same short name, and several distinct people may share it.  Every
credential binds a short name to a public-key fingerprint; only the fingerprint
decides *who* someone is.

**Signatures via a mature library.**  Ed25519 sign/verify is delegated to the
OpenSSL CLI (`museum/crypto.py`).  Signed bytes are canonical UTF-8 JSON
(sorted keys, tight separators), so both sides derive identical bytes and an
archive re-verifies without any live service.

**Collection scope is exact set math.**  A scope is a set of catalog items for
one owning museum, expressed as per-series integer intervals
(`COL-0000..` → sequences 0–9999).  This makes `⊆`, `∩` and membership precise,
which is what turns "a referral may only shrink authority" from a slogan into
an enforceable rule.

**Two possible routes — each receiver picks exactly one.**

* ** Route A — step-by-step (`Receiver.route = ROUTE_STEP`).**  The receiver
  holds the referral edges plus its own trust roots and searches the graph
  itself.  A path is valid only if every edge is correctly signed, the first
  issuer is a trusted root, key fingerprints and name mappings chain hop by
  hop, the subject is constant, and the receiver's own key ends the path.
  Authority along the path is the **intersection** of every edge's
  scope ⊆, purposes ∩ and validity ∩.  *All* simple paths are enumerated, so a
  dead path never hides a referral that holds elsewhere.  Cycles and
  back-references can only shrink, never widen, the result.  The root also
  fixes a delegation budget and may set `can_refer=false`; an intermediate
  cannot amplify either.

* **Route B — trusted translation desk (`Receiver.route = ROUTE_DESK`).**  A
  desk jointly acknowledged by both parties independently checks the *whole*
  path and then signs a **one-time** statement bound to `{station, document
  digest, purpose}` (plus the resolved name and the effective scope/validity).
  The receiver needs only its own trust root and that statement — it does not
  keep the origin chains.  The trade-off, by construction: **if the desk is
  offline, no new item can be re-credentialed.**  The desk re-verifies the path
  itself and refuses if a single path does not supply both name and authority.

Only one route is active per receiver; `receiver.route` chooses it.

**One path, or nothing.**  `authorize_stepwise` accepts a document only if a
**single** path simultaneously (a) binds the subject fingerprint to the signer,
(b) carries the needed purpose, (c) covers the item in its scope, and (d)
contains the signing time.  A name-only edge and a scope-only edge cannot be
spliced together (see `TestNoSplicing`).

**One-time, station-bound acceptance material** (`OneTimeAcceptanceMaterial`)
is bound to `(station, document digest, purpose, signer fingerprint, expiry,
nonce)`.  Handing the original museum's material to a different museum cannot
let a same-named person pass as the original signer — the station and
fingerprint no longer match (`TestOneTimeMaterial`).

**Subledger separation.**  The ledger keys items by a stable per-copy id, so a
call number may live in several subledgers (分册) and moving one item between
subledgers stays a separable operation on that item
(`TestSubledgerSplit`).

## Files

| file | role |
|------|------|
| `museum/crypto.py`   | canonical JSON + Ed25519 via OpenSSL CLI |
| `museum/model.py`    | scopes, validity, registration, referral, document, one-time material |
| `museum/issue.py`    | `Museum` (registry + referral minting), `Librarian` (document signing, material spending) |
| `museum/authority.py`| graph search, path composition, per-document authorization, translation desk |
| `museum/receiver.py` | `Receiver`, local ledger, filing, archival `Dossier` + successor re-check |
| `museum/demo.py`     | narrated end-to-end scenario |

## Requirement → test map

Each acceptance clause maps to a test class/assertion:

| 需求 (requirement) | where |
|---|---|
| 不同馆可登记同一短名；同短名不同人由键区分 | `TestRegistrationAndReferral.test_distinct_people_share_short_name` |
| 名称映射进自己编号空间，限定前缀/用途/有效期，可排除号段 | `CollectionScope.of(..., excludes=)`, `TestAcceptanceDrill.test_scope_exception_is_exact_not_overbroad` |
| 凭证/文书/授权可离线交接，成熟签名库 | `crypto.py` (OpenSSL) + `TestOfflineEvidence`, `TestRegistrationAndReferral.test_referral_signatures_verify_offline` |
| 验收人发“重叠前缀+一条例外”的引荐 | `Fixture.setUp` |
| 一张目录更正署入正确馆藏；一张展品凭单留原件待证 | `test_catalog_correction_filed_into_catalog`, `test_exhibit_claim_in_excluded_range_held_for_evidence` |
| 收件馆只持自己的信任根、选定路线的材料、当地待办 | `Receiver.file` / `ROUTE_DESK` |
| 接班人用归档原签字文件与随件依据复查 | `TestSuccessorRecheck`, `TestOfflineEvidence` |
| 只令一个中间馆缩短前缀，即使全真也改变接件结论 | `test_single_intermediate_narrows_prefix_changes_decision`, `test_hop_budget_capped_by_root` |
| 不能向第一条借名字、向第二条借签领资格 | `TestNoSplicing` |
| 回引/重叠映射不得放大可认范围；单一路径不通不丢别处成立者 | `TestNoAmplification`, `test_multiple_paths_one_dead_other_valid`, `test_cycle_does_not_loop_forever` |
| 还原引荐，只把一件藏品迁至同号另一分册，可分开办理 | `TestSubledgerSplit` |
| 原馆专用一次接件材料转交新馆，不能冒认同名签字人 | `TestOneTimeMaterial.test_rehoming_material_to_new_museum_fails` |
| 翻译处路线：离线则新件无法换证；限本站/摘要/用途 | `TestTranslationDesk`, `TestDeskIndependence` |

## Security properties, explicitly

* Forged or altered referrals fail: any change to a signed field invalidates
  the Ed25519 signature; registrations are re-checked against the issuer key
  embedded in the edge (`test_tampered_referral_rejected`,
  `test_registration_cannot_be_forged_without_registry_key`).
* Untrusted issuers cannot act as roots (`test_trusted_root_must_sign_the_first_edge`).
* Same-named, different-keyed receivers are rejected (`test_receiver_must_be_exact_key`).
* Validity, purpose and scope all narrow along a chain
  (`test_single_intermediate_narrows_prefix_changes_decision`).
* The translation desk verifies the path independently and refuses to issue
  when one path cannot supply both name and authority.

## Notes & limits

* Keys are generated fresh per process; the numbers printed by the demo differ
  between runs, but every *decision* is a deterministic function of the
  material presented.  To pin keys for a long-term archive, persist the
  PEM/DER bytes (the API already carries public keys inline in every
  credential).
* The `_verify_cache` is process-local and keyed by content hashes; it only
  speeds up repeated checks and never changes a verdict.
* This is a drill/reference implementation, not a production PKI: there is no
  key rotation, revocation list, or hardware key store.  Those are natural
  extensions, but out of scope for the exercise.
