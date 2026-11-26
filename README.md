# 隐私发布台 / 维修预约台 (Privacy Publishing & Repair Booking Desk)

A local, dependency-free Python toolkit for a housing cooperative that collects
voluntary weekly "有噪声 / 无噪声" (noise / no-noise) returns. It has two
desks:

* a **publishing desk** (`Publisher`, `Session.publish`) that turns raw return
  slips plus already-mailed paper reports into *public receipts* — exact counts
  or truthfully-labelled ranges — never exposing a protected household;
* a **repair booking desk** (`BookingDesk`, `Session.book`) that lets a
  maintenance worker reserve a work slot **only** from an exact, signed-out
  receipt.

The reviewer and the worker hand the same state back and forth as a JSON
session file, so the whole flow runs on one machine.

---

## The privacy standard (exact wording)

> Given only what has already been made public **this week** — published exact
> counts, published truthfully-labelled ranges, the household IDs each item
> covers, and public facts (which household is in which group) — every
> protected household must still have **at least two** possible answers.

Formally: collect every public `Constraint` for the week (published **and**
mailed, corrections included), enumerate all binary assignments of households
to `{0,1}` consistent with them, and require that no protected household has
the same value across *all* admissible assignments.

* Two exact counts that compose into a leak are caught: with `甲+乙=1` and
  `甲+乙+丙=1`, the whole-floor count minus the north-wing count forces `丙=0`.
  The engine enumerates assignments and sees only one value for 丙, so the
  second publication is refused as exact and downgraded to a range.
* Only **public** information counts. The publisher knows the private truth to
  issue truthful numbers, but the safety check (`privacy.py`) reasons over
  constraints alone and never reads the answers.

`privacy_desk/privacy.py` implements the whole thing; `is_safe`,
`possible_values`, `forced_answers`, `tightest_safe_range` are the primitives.

---

## Public information is append-only

`Ledger` has **no** delete / remove / retract / replace / clear. A mailed
number is public the instant it leaves the building, so a same-week correction
can only *append* — it can never make an earlier figure vanish
(`run_correction_demo`, `tests/test_ledger.py::test_correction_cannot_remove_earlier_number`).

---

## Two service policies

The brief allows the desk to behave in two ways; both are implemented and
selectable per session.

### `policy="arrival"` — first served keeps precision

Requests are processed in arrival order. Each tries its **exact** count; if
that exact count would expose someone *given everything already public*, the
desk falls back to the **tightest truthfully-labelled range**. Whoever asks
first keeps usable precision; the later party bears the cost.

Replay week 1 both ways (`replay_week1()`):

| order | 北翼 (租户) | 整层 (物业) | who gets exact | who books |
|---|---|---|---|---|
| tenant first  | `exact = 1`      | `range [1,2]` | 租户 | 租户 (入户时段) |
| property first| `range [0,1]`    | `exact = 1`   | 物业 | 物业 (入楼检修) |

### `policy="preagreed"` — reserve exact for a pre-agreed group

Pre-agree a set of **pairwise non-overlapping** groups to keep exact precision
for (typically the ones with booking value). Requests for a reserved group are
served exact whenever possible; the other party only ever receives a reference
range. Overlapping reservations are rejected
(`test_preagreed_rejects_overlapping_reservations`) because two exact counts
over overlapping groups can compose into a leak.

`run_preagreed("北翼")` / `run_preagreed("整层")` show the two symmetric
outcomes.

> "至少有一方能安全取得确数时，不能只印空白周报."
> The publisher only emits a *blank* range `[0, n]` when no exact could be
> safe; whenever an exact is safe for the requester it is issued
> (`test_exact_alone_safe_so_not_blank`, `test_week1_never_blank_when_exact_possible`).

---

## Week-2 replays (from "north-wing report not yet mailed")

`replay_week2()` returns three cases:

1. **No old reports.** Public history empty → 北翼 exact is safe → tenant books
   the entry repair (`got_exact=True`, `booked=True`).
2. **One already-mailed 整层 exact imported.** With `甲+乙+丙=1` already
   public, sending `甲+乙=1` would derive 丙, so the desk issues a range and the
   tenant **cannot** book (`got_exact=False`, `booked=False`).
3. **Blank history, but the entry slot is already occupied.** The weekly report
   still goes out as exact; the booking is refused on the occupied slot and the
   tenant **reschedules** to a free slot (`report_mailed=True`,
   `final_booking.slot == "入户时段-下午"`).

### Late discovery

`run_late_discovery()` mails **both** exact reports first, *then* notices the
omission. It records faithfully in the audit log that 丙's answer (无噪声 / 0)
is now derivable. Corrections cannot undo it — the ledger only appends.

---

## Acceptance: re-enter a week with ≤ 8 households

```python
from privacy_desk import WeekConfig, run_generic_week

cfg = WeekConfig(
    week_id="G",
    households=("a", "b", "c", "d"),
    groups={"ab": ("a", "b"), "abc": ("a", "b", "c"), "cd": ("c", "d")},
    protected=("a", "b", "c", "d"),
)
result = run_generic_week(
    cfg,
    {"a": 1, "b": 0, "c": 0, "d": 1},
    [("x", "ab"), ("y", "abc"), ("z", "cd")],
)  # optionally add booking attempts: bookings=[("x","ab","s1"), ...]
assert result.data["safe"] and result.data["derived"] == {}
```

`run_generic_week` refuses more than eight households
(`MAX_HOUSEHOLDS`), matching the acceptance limit. Pass
`bookings=[(requester, group, slot), ...]` to also exercise the booking desk:
each attempt uses that requester's latest receipt for the group, so a party
that received a range gets a recorded refusal while an exact holder books.

---

## Command line

```bash
export PYTHONPATH=/workspace

# reviewer starts a week from a raw spec file (truth + groups)
python3 -m privacy_desk.cli new --spec spec.json --out sess.json

# reviewer imports an already-mailed paper report (exact or range)
python3 -m privacy_desk.cli mail --session sess.json --group 整层 --exact 1
python3 -m privacy_desk.cli mail --session sess.json --group 整层 --range 0 3

# reviewer submits a new publication request (exact, auto-downgraded if unsafe)
python3 -m privacy_desk.cli publish --session sess.json --group 北翼 --who 租户
python3 -m privacy_desk.cli publish --session sess.json --group 整层 --who 物业 --no-exact

# worker books a work slot from a signed-out exact receipt
python3 -m privacy_desk.cli book --session sess.json \
    --receipt W1-published-001 --who 租户 --slot 入户时段

# inspect: public state, derivations (leaks), bookings
python3 -m privacy_desk.cli report --session sess.json

# record a late-discovered breach faithfully
python3 -m privacy_desk.cli audit --session sess.json

# replay every worked scenario from the brief
python3 -m privacy_desk.cli demo
```

`spec.json` shape:

```json
{
  "week_id": "W1",
  "households": ["甲", "乙", "丙"],
  "groups": {"北翼": ["甲", "乙"], "整层": ["甲", "乙", "丙"]},
  "answers": {"甲": 1, "乙": 0, "丙": 0},
  "protected": ["甲", "乙", "丙"],
  "policy": "arrival",
  "reserved_groups": []
}
```

`answers` is the private truth (1 = 有噪声/投诉, 0 = 无噪声). It is kept out of
every public receipt and out of the safety calculus.

---

## Layout

```
privacy_desk/
  model.py       Constraint / Receipt / Booking / WeekConfig + validation
  privacy.py     the safety engine (admissibility, forcing, range search)
  ledger.py      append-only public receipts + audit notes
  publisher.py   publishing desk (arrival + pre-agreed policies)
  booking.py     repair booking desk (exact receipts only, reschedule)
  session.py     persistent reviewer/worker hand-off (JSON)
  scenarios.py   every worked replay from the brief
  cli.py         command-line peer
tests/           unit + property tests
run_tests.py     stdlib test runner (no pytest needed here)
```

## Running tests

pytest is not installed and there is no network, so a tiny stdlib runner is
provided. It supports the subset the suite uses (`test_*` functions,
`pytest.raises`):

```bash
python3 run_tests.py
```

If pytest *is* available, `python3 -m pytest tests/ -q` works too (the tests
import `pytest` only for `raises`).
