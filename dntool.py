#!/usr/bin/env python3
"""
dntool.py -- local design-swap tool for anonymized routing-change requests.

Scenario (a "small-width" exchange)
-----------------------------------
Two synthetic partitions A and B re-use the SAME short address block
10.0.0.0/28 (16 addresses).  Each partition is its own forwarding table
(a VRF), so the same address routes differently in A and B.

The machine room trusts a contractor to reshape only *part* of partition A:
a trusted scope (10.0.0.0/29 = addresses .0..7).  The contractor must not
touch: partition B, A's untrusted half (.8..15), the cross-partition/transit
nodes, or anything outside the scope.

The machine room hands the contractor an ANONYMIZED editable design:

  * real in-scope prefixes -> fictitious prefixes under 198.18.0.0/15 that
    preserve the containment relations of the scope (a /30 stays a /30);
  * real interfaces -> opaque numeric tokens (with a behavioural label);
  * private node / destination names -> never emitted;
  * everything outside the scope -> not emitted at all.  The manifest records
    *that* a hidden range exists (an opaque count/label), never *which* real
    prefix or node it is on.

Two editing surfaces are provided, both landing on the same underlying edit:
  (1) FAKE-PREFIX view  -- edit the fictitious prefix rows;
  (2) ANON-GROUP view   -- set an outcome ("direct" / "L1" / "L2") per group.

The machine room then:
  * preloads the edit on the REAL table,
  * rejects the WHOLE change if any part is not expressible unambiguously,
  * reports which real segments and which internal probe flows are affected,
  * keeps per-change-number trial results for later review.

Subcommands: init, package, check, groups, trial, apply, route, round,
restore, unhide, refresh-return, alias, reprioritize, review, walkthrough.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Net = ipaddress.IPv4Network
Addr = ipaddress.IPv4Address

DEFAULT_SEED = 1429
FAKE_POOL = "198.18.0.0/15"          # RFC 2544 benchmarking range, reserved
ACTIVE = "ACTIVE"                     # static route tag

REAL = "real.json"
ORIG = "original.json"
CFG = "config.json"
PKG = "package.anon"
MANIFEST = "package.manifest.json"
SECRET_MAP = "map.secret.json"
REVIEW = "review.jsonl"


class ToolError(Exception):
    pass


class Unrepresentable(ToolError):
    pass


# --------------------------------------------------------------------------- #
#  Routes and table lookup
# --------------------------------------------------------------------------- #
@dataclass
class Route:
    part: str          # "A" or "B"
    prefix: Net
    ifc: int           # (AT? neat) destination interface, local to the part
    nbr: str = ""      # private node name, "" == directly connected
    pri: int = 100     # priority among equal-length prefixes
    tag: str = ACTIVE
    ttl: int = 0       # >0: expiry counter, decremented each round

    def to_json(self):
        return {"part": self.part, "prefix": str(self.prefix), "ifc": self.ifc,
                "nbr": self.nbr, "pri": self.pri, "tag": self.tag, "ttl": self.ttl}

    @staticmethod
    def from_json(d):
        return Route(d["part"], Net(d["prefix"]), int(d["ifc"]), d.get("nbr", ""),
                     int(d.get("pri", 100)), d.get("tag", ACTIVE), int(d.get("ttl", 0)))

    def copy(self, **kw) -> "Route":
        d = self.to_json()
        d.update(kw)
        return Route.from_json(d)


def lpm(routes: List[Route], part: str, addr: str) -> Optional[Route]:
    a = Addr(addr)
    cands = [r for r in routes if r.part == part and a in r.prefix]
    if not cands:
        return None
    return max(cands, key=lambda r: (r.prefix.prefixlen, r.pri, -r.ifc,
                                     r.nbr if r.nbr else ""))


def addresses(space: Net) -> List[str]:
    return [str(a) for a in space]


# --------------------------------------------------------------------------- #
#  Real-side configuration  (machine room only; never handed to contractor)
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    space: Net                 # the whole small block both partitions re-use
    scope: Net                 # trusted scope inside partition A
    fake_scope: Net            # fictitious mirror of the scope
    untrusted: Net             # A's non-delegated half (hidden)
    allowed_ifcs: List[int]    # destinations the contractor may route to
    landing_names: Dict[str, int]   # behavioural label -> real interface
    retn: Net                  # return/transit range (expires), hidden
    routes: List[Route]
    groups: List[dict]         # behavioural anon groups over fake prefixes

    def to_json(self):
        return {"space": str(self.space), "scope": str(self.scope),
                "fake_scope": str(self.fake_scope), "untrusted": str(self.untrusted),
                "allowed_ifcs": self.allowed_ifcs,
                "landing_names": self.landing_names,
                "retn": str(self.retn),
                "routes": [r.to_json() for r in self.routes],
                "groups": self.groups}

    @staticmethod
    def from_json(d) -> "Config":
        return Config(Net(d["space"]), Net(d["scope"]), Net(d["fake_scope"]),
                      Net(d["untrusted"]), list(map(int, d["allowed_ifcs"])),
                      dict(d["landing_names"]), Net(d["retn"]),
                      [Route.from_json(r) for r in d["routes"]], d["groups"])


def default_config() -> Config:
    space = Net("10.0.0.0/28")           # 16 addresses, small width
    scope = Net("10.0.0.0/29")           # trusted: .0 .. .7
    fake_scope = Net("198.18.0.0/29")    # fictitious mirror
    untrusted = Net("10.0.0.8/29")       # A's non-delegated half
    retn = Net("10.0.0.8/29")            # return/transit range (expires)
    routes = [
        # ---- partition A ----
        Route("A", Net("10.0.0.0/29"), 10, "", 200, ACTIVE),          # scope default
        Route("A", Net("10.0.0.0/30"), 10, "", 200, ACTIVE),          # segment S0 .0-.3
        Route("A", Net("10.0.0.4/30"), 10, "", 200, ACTIVE),          # segment S1 .4-.7
        Route("A", Net("10.0.0.1/32"), 20, "", 10, ACTIVE),           # landing L1 (.1)
        Route("A", Net("10.0.0.1/32"), 21, "", 10, ACTIVE),           # landing L2 (.1)
        Route("A", Net("10.0.0.6/32"), 20, "", 10, ACTIVE),           # landing L1b (.6)
        Route("A", Net("10.0.0.8/29"), 0, "coreA", 200, "return", 2), # transit, expiring
        Route("A", Net("10.0.0.8/29"), 25, "", 200, ACTIVE),           # idle alternate (loses tie)
        # ---- partition B (same short addresses, different table) ----
        Route("B", Net("10.0.0.0/29"), 30, "", 200, ACTIVE),
        Route("B", Net("10.0.0.0/30"), 30, "", 200, ACTIVE),
        Route("B", Net("10.0.0.4/30"), 31, "", 200, ACTIVE),
        Route("B", Net("10.0.0.8/29"), 31, "", 200, ACTIVE),
    ]
    groups = [
        {"name": "scope-default", "fake": ["198.18.0.0/29"]},
        {"name": "segment-a", "fake": ["198.18.0.0/30"]},
        {"name": "segment-b", "fake": ["198.18.0.4/30"]},
    ]
    landing_names = {"direct": 10, "L1": 20, "L2": 21}
    return Config(space, scope, fake_scope, untrusted, [10, 20, 21],
                  landing_names, retn, routes, groups)


# --------------------------------------------------------------------------- #
#  Real state
# --------------------------------------------------------------------------- #
@dataclass
class RealState:
    routes: List[Route]
    epoch: int = 0
    rounds: int = 0
    changes: int = 0

    def to_json(self):
        return {"epoch": self.epoch, "rounds": self.rounds,
                "changes": self.changes,
                "routes": [r.to_json() for r in self.routes]}

    @staticmethod
    def from_json(d) -> "RealState":
        return RealState([Route.from_json(r) for r in d["routes"]],
                         d.get("epoch", 0), d.get("rounds", 0), d.get("changes", 0))

    def copy(self) -> "RealState":
        return RealState([r.copy() for r in self.routes], self.epoch,
                         self.rounds, self.changes)


def part_routes(state: RealState, part: str) -> List[Route]:
    return [r for r in state.routes if r.part == part]


def tables_equal(state: RealState, other: RealState, part: str) -> bool:
    a = sorted((str(r.prefix), r.ifc, r.nbr, r.pri, r.tag, r.ttl)
               for r in state.routes if r.part == part)
    b = sorted((str(r.prefix), r.ifc, r.nbr, r.pri, r.tag, r.ttl)
               for r in other.routes if r.part == part)
    return a == b


# --------------------------------------------------------------------------- #
#  Anonymization mapping  (prefix mirror + interface tokens)
# --------------------------------------------------------------------------- #
def mirror(cfg: Config, real: Net) -> Net:
    if not real.subnet_of(cfg.scope):
        raise Unrepresentable(f"{real} is not inside the trusted scope {cfg.scope}")
    off = int(real.network_address) - int(cfg.scope.network_address)
    return Net((int(cfg.fake_scope.network_address) + off, real.prefixlen))


def unmirror(cfg: Config, fake: Net) -> Net:
    if not fake.subnet_of(cfg.fake_scope):
        raise Unrepresentable(f"{fake} is not inside the fictitious scope {cfg.fake_scope}")
    off = int(fake.network_address) - int(cfg.fake_scope.network_address)
    real = Net((int(cfg.scope.network_address) + off, fake.prefixlen))
    if not real.subnet_of(cfg.scope):
        raise Unrepresentable(f"{fake} maps outside the trusted scope")
    return real


def build_tokens(cfg: Config) -> Tuple[Dict[int, int], Dict[int, int]]:
    """Deterministic interface-token bijection, stable across rounds."""
    ifcs = sorted({r.ifc for r in cfg.routes
                   if r.part == "A" and r.prefix.subnet_of(cfg.scope)})
    ifc2tok = {ifc: i + 1 for i, ifc in enumerate(ifcs)}
    tok2ifc = {v: k for k, v in ifc2tok.items()}
    return ifc2tok, tok2ifc


def scope_routes(cfg: Config, state: RealState) -> List[Route]:
    """Partition-A routes whose prefix lies entirely within the trusted scope."""
    return [r for r in state.routes if r.part == "A" and r.prefix.subnet_of(cfg.scope)]


def build_package(cfg: Config, state: RealState) -> List[dict]:
    """The anonymized, editable design rows the contractor receives."""
    ifc2tok, _ = build_tokens(cfg)
    rows = []
    for r in sorted(scope_routes(cfg, state), key=lambda r: (r.prefix, r.ifc)):
        rows.append({
            "prefix": str(mirror(cfg, r.prefix)),
            "ifc": ifc2tok[r.ifc],
            "nbr": 0,                      # contractor may only choose 'direct'
            "pri": r.pri,
            "tag": r.tag,
        })
    return rows


def dump_package(cfg: Config, rows: List[dict]) -> str:
    labels = {v: k for k, v in cfg.landing_names.items()}
    out = [
        "# ANON DESIGN v1  (editable: prefix / ifc-token / pri)",
        "# fictitious prefixes preserve the containment of the trusted scope",
        "# ifc tokens: " + ", ".join(f"{t}={labels.get(i, 'hidden')}"
                                     for i, t in sorted(build_tokens(cfg)[0].items(),
                                                        key=lambda kv: kv[1])),
        "# nbr token 0 = direct.  Do NOT use tokens or prefixes not listed here.",
        "# ---",
    ]
    for r in rows:
        out.append(f"{r['prefix']} ifc={r['ifc']} nbr={r['nbr']} "
                   f"pri={r['pri']} tag={r['tag']}")
    return "\n".join(out) + "\n"


def parse_package(text: str) -> List[dict]:
    rows = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        prefix = parts[0]
        fields = {}
        for kv in parts[1:]:
            if "=" not in kv:
                raise ToolError(f"bad design line: {ln!r}")
            k, v = kv.split("=", 1)
            fields[k] = v
        try:
            rows.append({"prefix": prefix, "ifc": int(fields.get("ifc", "0")),
                         "nbr": int(fields.get("nbr", "0")),
                         "pri": int(fields.get("pri", "100")),
                         "tag": fields.get("tag", ACTIVE)})
        except ValueError:
            raise ToolError(f"bad design line: {ln!r}")
    return rows


# --------------------------------------------------------------------------- #
#  Ambiguity analysis
# --------------------------------------------------------------------------- #
def candidates(cfg: Config, real: Net, parts: Optional[List[str]] = None) -> List[Route]:
    """All real routes whose prefix equals `real`, optionally across partitions.

    Used to prove that a change is only expressible unambiguously when the
    trusted partition is fixed: across A and B the same short address yields
    two distinct real rows.
    """
    parts = parts if parts is not None else ["A", "B"]
    return [r for r in cfg.routes
            if r.part in parts and r.prefix == real]


# --------------------------------------------------------------------------- #
#  Apply pipeline  (preload -> verify -> commit or reject whole change)
# --------------------------------------------------------------------------- #
@dataclass
class ApplyReport:
    accepted: bool
    reasons: List[str] = field(default_factory=list)
    real_edits: List[dict] = field(default_factory=list)
    affected_probes: List[dict] = field(default_factory=list)
    ambiguous: List[dict] = field(default_factory=list)
    before: List[dict] = field(default_factory=list)
    after: List[dict] = field(default_factory=list)
    epoch: int = 0
    change: str = ""


def probes(cfg: Config) -> List[Tuple[str, str]]:
    """Exhaustive probe grid: every address of the small width, in A and B."""
    return [(p, a) for p in ("A", "B") for a in addresses(cfg.space)]


def trial_table(cfg: Config, state: RealState) -> List[dict]:
    out = []
    for part, addr in probes(cfg):
        hit = lpm(state.routes, part, addr)
        out.append({
            "part": part, "addr": addr,
            "prefix": str(hit.prefix) if hit else None,
            "ifc": hit.ifc if hit else None,
            "nbr": hit.nbr if hit else None,
            "via": ("direct" if (hit and not hit.nbr) else
                    ("transit" if hit else "no-route")),
        })
    return out


def _scope_covered(cfg: Config, design: List[dict]) -> bool:
    """Every address of the trusted scope must still be covered by the design."""
    rem = int(cfg.scope.num_addresses)
    covered = set()
    for r in design:
        try:
            real = unmirror(cfg, Net(r["prefix"]))
        except Unrepresentable:
            continue
        if real.subnet_of(cfg.scope):
            for a in real:
                covered.add(int(a))
    return len(covered) == int(cfg.scope.num_addresses)


def apply_package(cfg: Config, state: RealState, design: List[dict],
                  change: str = "C0") -> Tuple[RealState, ApplyReport]:
    """Translate the anonymous design back and apply it, atomically."""
    rep = ApplyReport(accepted=False, change=change, epoch=state.epoch)
    _, tok2ifc = build_tokens(cfg)
    landing_tokens = {t for i, t in build_tokens(cfg)[0].items()
                      if i in cfg.allowed_ifcs}

    # 1. translate + expressibility checks
    translated: List[Route] = []
    real_edits: List[dict] = []
    amb: List[dict] = []
    for d in design:
        # ifc / destination must be an allowed landing token
        if d["ifc"] not in landing_tokens:
            rep.reasons.append(f"destination token {d['ifc']} is not an allowed landing")
            continue
        if d["nbr"] != 0:
            rep.reasons.append("neighbour token other than 0 (direct) is not permitted")
            continue
        try:
            fake = Net(d["prefix"])
            real = unmirror(cfg, fake)
        except (Unrepresentable, ValueError) as e:
            rep.reasons.append(f"prefix {d['prefix']}: {e}")
            continue
        if not real.subnet_of(cfg.scope):
            rep.reasons.append(f"prefix {real} lies outside the trusted scope")
            continue
        # ambiguity: across both partitions this short address has >1 candidate
        cand = candidates(cfg, real)
        if len(cand) > 1:
            amb.append({"real": str(real), "candidates": [
                f"{c.part}:ifc{c.ifc}" for c in cand]})
        ifc = tok2ifc[d["ifc"]]
        translated.append(Route("A", real, ifc, "", d["pri"], d["tag"]))
        real_edits.append({"fake": d["prefix"], "real": str(real),
                           "ifc_token": d["ifc"], "real_ifc": ifc, "pri": d["pri"]})

    if rep.reasons:
        rep.reasons = list(dict.fromkeys(rep.reasons))
        rep.real_edits = real_edits
        rep.ambiguous = amb
        return state, rep

    if not _scope_covered(cfg, design):
        rep.reasons.append("design no longer covers the whole trusted scope")
        rep.real_edits = real_edits
        rep.ambiguous = amb
        return state, rep

    # 2. build candidate table: replace in-scope A rows with the translated set,
    #    leave every other A row and every B row untouched.
    new_routes = [r.copy() for r in state.routes
                  if not (r.part == "A" and r.prefix.subnet_of(cfg.scope))]
    new_routes.extend(translated)
    cand_state = state.copy()
    cand_state.routes = new_routes

    # 3. invariants
    #   (a) partition B untouched
    if not tables_equal(state, cand_state, "B"):
        rep.reasons.append("change would alter partition B (sibling must keep original line)")
        return state, rep
    #   (b) out-of-scope A addresses unchanged
    before = trial_table(cfg, state)
    after = trial_table(cfg, cand_state)
    for b, a in zip(before, after):
        inside = (b["part"] == "A" and Addr(b["addr"]) in cfg.scope)
        if not inside and (b["prefix"], b["ifc"], b["via"]) != (a["prefix"], a["ifc"], a["via"]):
            rep.reasons.append(f"change leaks outside the trusted scope at {b['part']}:{b['addr']}")
            return state, rep

    # 4. commit
    affected = []
    for b, a in zip(before, after):
        if (b["prefix"], b["ifc"], b["via"]) != (a["prefix"], a["ifc"], a["via"]):
            affected.append({"part": a["part"], "addr": a["addr"],
                             "before": f"{b['prefix']} ifc{b['ifc']}",
                             "after": f"{a['prefix']} ifc{a['ifc']}"})
    cand_state.epoch = state.epoch + 1
    cand_state.changes = state.changes + 1
    rep.accepted = True
    rep.real_edits = real_edits
    rep.affected_probes = affected
    rep.ambiguous = amb
    rep.before = before
    rep.after = after
    rep.epoch = cand_state.epoch
    return cand_state, rep


# --------------------------------------------------------------------------- #
#  Persistence
# --------------------------------------------------------------------------- #
def save_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=False)


def read_text(path: str) -> str:
    with open(path) as f:
        return f.read()


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_state(state: RealState):
    save_json(REAL, state.to_json())


def load_state() -> RealState:
    try:
        return RealState.from_json(load_json(REAL))
    except FileNotFoundError:
        raise ToolError("no working state; run `init` first")


def load_cfg() -> Config:
    try:
        return Config.from_json(load_json(CFG))
    except FileNotFoundError:
        raise ToolError("no config; run `init` first")


def record_review(report: ApplyReport, summary: str, decision: str,
                  extra: Optional[dict] = None):
    rec = {
        "change": report.change, "decision": decision, "summary": summary,
        "epoch": report.epoch,
        "affected": report.affected_probes,
        "real_edits": report.real_edits,
        "ambiguous": report.ambiguous,
        "reasons": report.reasons,
        "trials": report.after,
    }
    if extra:
        rec.update(extra)
    with open(REVIEW, "a") as f:
        f.write(json.dumps(rec) + "\n")


def next_change_id(state: RealState, prefix="C") -> str:
    return f"{prefix}{state.changes + 1}"


# --------------------------------------------------------------------------- #
#  Command implementations
# --------------------------------------------------------------------------- #
def cmd_init(args):
    cfg = default_config()
    if args.config:
        cfg = Config.from_json(load_json(args.config))
    state = RealState([r.copy() for r in cfg.routes], epoch=0, rounds=0, changes=0)
    save_json(CFG, cfg.to_json())
    save_state(state)
    save_json(ORIG, state.to_json())
    rows = build_package(cfg, state)
    with open(PKG, "w") as f:
        f.write(dump_package(cfg, rows))
    ifc2tok, tok2ifc = build_tokens(cfg)
    labels = {v: k for k, v in cfg.landing_names.items()}
    save_json(MANIFEST, {
        "space": str(cfg.space), "trusted_scope": str(cfg.scope),
        "fake_scope": str(cfg.fake_scope),
        "visible_prefixes": [r["prefix"] for r in rows],
        "ifc_tokens": {str(t): labels.get(i, "hidden") for i, t in ifc2tok.items()},
        "hidden": [
            {"label": "partition B", "real_ranges": len([
                r for r in cfg.routes if r.part == "B"])},
            {"label": "untrusted half of A", "real_ranges": 1},
            {"label": "return/transit range", "real_ranges": 1},
            {"label": "private transit/nbr nodes", "real_ranges": len(
                [r for r in cfg.routes if r.nbr])},
        ],
        "groups": cfg.groups,
        "note": "internal manifest; contractor receives package.anon only",
    })
    save_json(SECRET_MAP, {"ifc2tok": {str(k): v for k, v in ifc2tok.items()},
                           "tok2ifc": {str(k): v for k, v in tok2ifc.items()}})
    print(f"initialized: {len(cfg.routes)} real routes, trusted scope {cfg.scope}")
    print(f"wrote anonymized design -> {PKG}  (real unit {cfg.scope} -> fake unit {cfg.fake_scope})")
    return 0


def cmd_package(args):
    cfg = load_cfg()
    state = load_state()
    rows = build_package(cfg, state)
    text = dump_package(cfg, rows)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


def cmd_check(args):
    """Leak scan: reject any contractor package carrying originals / private detail."""
    cfg = load_cfg()
    try:
        text = read_text(args.file)
    except FileNotFoundError:
        raise ToolError(f"no such file: {args.file}")
    leaks = []
    forbidden = [str(cfg.scope), str(cfg.untrusted), "10.0.0.", "coreA",
                 "192.0.2.", "B:"]
    # the plain '10.0.0.' scan is enough to catch any raw address of this space
    for tok in forbidden:
        if tok in text:
            leaks.append(tok)
    if leaks:
        print("LEAK DETECTED:", sorted(set(leaks)))
        return 2
    print("OK: no raw addresses, private node names or out-of-domain detail found")
    return 0


def cmd_groups(args):
    cfg = load_cfg()
    labels = {i: k for k, i in cfg.landing_names.items()}
    if args.anon:
        print("# anonymized groups handed to the contractor")
        print("# outcome tokens:", ", ".join(f"{t}={labels.get(i, 'hidden')}"
                                              for i, t in build_tokens(cfg)[0].items()))
        for g in cfg.groups:
            print(f"{g['name']:14} rows={g['fake']}")
        return 0
    for g in cfg.groups:
        print(f"{g['name']:14} rows={g['fake']}")
    return 0


def cmd_trial(args):
    cfg = load_cfg()
    state = load_state()
    print(f"# epoch={state.epoch} rounds={state.rounds}")
    print_trial(cfg, trial_table(cfg, state))
    return 0


def print_trial(cfg: Config, table: List[dict]):
    print(f"{'part':5}{'addr':16}{'prefix':18}{'ifc':>5}{'via':>10}")
    for t in table:
        print(f"{t['part']:5}{t['addr']:16}{str(t['prefix']):18}"
              f"{str(t['ifc']):>5}{t['via']:>10}")


def _resolve_design_path(args) -> str:
    return args.design if getattr(args, "design", None) else PKG


def cmd_apply(args):
    cfg = load_cfg()
    state = load_state()
    path = _resolve_design_path(args)
    try:
        design = parse_package(read_text(path))
    except FileNotFoundError:
        raise ToolError(f"no such design file: {path}")
    cid = args.change or next_change_id(state)
    new_state, rep = apply_package(cfg, state, design, change=cid)
    if not rep.accepted:
        print(f"REJECTED change {cid}: nothing applied (epoch stays {state.epoch})",
              file=sys.stderr)
        for reason in rep.reasons:
            print(f"  - {reason}", file=sys.stderr)
        if rep.ambiguous:
            print("  ambiguity across partitions:", rep.ambiguous, file=sys.stderr)
        record_review(rep, "apply (anonymous design)", "rejected")
        return 2
    save_state(new_state)
    save_json(SECRET_MAP, _secret_map(cfg))
    print(f"ACCEPTED change {cid}: epoch {state.epoch} -> {new_state.epoch}")
    print_real_edits(rep)
    print_affected(rep)
    record_review(rep, "apply (anonymous design)", "accepted")
    return 0


def print_real_edits(rep: ApplyReport):
    if not rep.real_edits:
        return
    print("real segments the change lands on:")
    for e in rep.real_edits:
        print(f"  {e['fake']} -> {e['real']}  (token {e['ifc_token']} = ifc {e['real_ifc']})")


def print_affected(rep: ApplyReport):
    print(f"affected internal probe flows: {len(rep.affected_probes)}")
    for a in rep.affected_probes:
        print(f"  {a['part']}:{a['addr']:16} {a['before']:20} -> {a['after']}")


def _secret_map(cfg: Config):
    ifc2tok, tok2ifc = build_tokens(cfg)
    return {"ifc2tok": {str(k): v for k, v in ifc2tok.items()},
            "tok2ifc": {str(k): v for k, v in tok2ifc.items()}}


def cmd_route(args):
    """ANON-GROUP view: set a group's outcome, then apply."""
    cfg = load_cfg()
    state = load_state()
    group = next((g for g in cfg.groups if g["name"] == args.group), None)
    if group is None:
        raise ToolError(f"unknown group {args.group!r}")
    try:
        ifc = cfg.landing_names[args.to]
    except KeyError:
        raise ToolError(f"unknown outcome {args.to!r}; choose {list(cfg.landing_names)}")
    ifc2tok, _ = build_tokens(cfg)
    tok = ifc2tok[ifc]
    design = build_package(cfg, state)
    targets = set(group["fake"])
    changed = 0
    for d in design:
        if d["prefix"] in targets:
            d["ifc"] = tok
            changed += 1
    if not changed:
        raise ToolError(f"group {args.group!r} matches no design row")
    text = dump_package(cfg, design)
    with open(PKG, "w") as f:
        f.write(text)
    cid = args.change or next_change_id(state)
    new_state, rep = apply_package(cfg, state, design, change=cid)
    if not rep.accepted:
        print(f"REJECTED change {cid}: {rep.reasons}", file=sys.stderr)
        record_review(rep, f"route {args.group}->{args.to}", "rejected")
        return 2
    save_state(new_state)
    print(f"ACCEPTED change {cid}: group {args.group} -> {args.to} (ifc {ifc})")
    print_real_edits(rep)
    print_affected(rep)
    record_review(rep, f"route {args.group}->{args.to}", "accepted")
    return 0


def cmd_divert(args):
    """FAKE-PREFIX view: divert one more-specific segment by editing an ifc token."""
    cfg = load_cfg()
    state = load_state()
    fake = Net(args.segment)
    try:
        ifc = cfg.landing_names[args.to]
    except KeyError:
        raise ToolError(f"unknown outcome {args.to!r}")
    ifc2tok, _ = build_tokens(cfg)
    design = build_package(cfg, state)
    hit = 0
    for d in design:
        if d["prefix"] == str(fake):
            d["ifc"] = ifc2tok[ifc]
            hit += 1
    if not hit:
        raise ToolError(f"segment {fake} is not an editable design prefix")
    with open(PKG, "w") as f:
        f.write(dump_package(cfg, design))
    cid = args.change or next_change_id(state)
    new_state, rep = apply_package(cfg, state, design, change=cid)
    if not rep.accepted:
        print(f"REJECTED change {cid}: {rep.reasons}", file=sys.stderr)
        record_review(rep, f"divert {fake}->{args.to}", "rejected")
        return 2
    save_state(new_state)
    print(f"ACCEPTED change {cid}: divert {fake} -> {args.to} (ifc {ifc})")
    print_real_edits(rep)
    print_affected(rep)
    record_review(rep, f"divert {fake}->{args.to}", "accepted")
    return 0


def cmd_round(args):
    """Advance one round: expiring (return) routes lose a unit and may vanish."""
    cfg = load_cfg()
    state = load_state()
    survivor = []
    expired = []
    for r in state.routes:
        if r.tag == "return" or r.ttl > 0:
            if r.ttl > 1:
                survivor.append(r.copy(ttl=r.ttl - 1))
            else:
                expired.append(r)
        else:
            survivor.append(r.copy())
    new = state.copy()
    new.routes = survivor
    new.rounds = state.rounds + 1
    save_state(new)
    print(f"round {new.rounds}: expired {[str(r.prefix) + ' ' + r.nbr for r in expired] or '(none)'}")
    lost = [a for a in addresses(cfg.space)
            if lpm(state.routes, "A", a) and not lpm(new.routes, "A", a)]
    print("A addresses that lost their applicable route:", lost or "(none)")
    if lost:
        print_trial(cfg, trial_table(cfg, new))
    return 0


def cmd_restore(args):
    cfg = load_cfg()
    try:
        orig = RealState.from_json(load_json(ORIG))
    except FileNotFoundError:
        raise ToolError("no original snapshot; run `init`")
    cur = load_state()
    orig.epoch = cur.epoch          # keep epoch counter monotonic
    orig.rounds = cur.rounds
    orig.changes = cur.changes
    save_state(orig)
    print(f"restored original routing (epoch kept at {orig.epoch})")
    print_trial(cfg, trial_table(cfg, orig))
    return 0


def cmd_reprioritize(args):
    """Only adjust a priority relation on the original graph."""
    cfg = load_cfg()
    state = load_state()
    target = Net(args.prefix)
    ifc = int(args.ifc)
    rows = [r for r in state.routes if r.part == "A" and r.prefix == target and r.ifc == ifc]
    if not rows:
        raise ToolError(f"no A route {target} on ifc {ifc}")
    for r in rows:
        r.pri = int(args.pri)
    save_state(state)
    print(f"reprioritized {target} ifc {ifc} -> pri {args.pri}")
    print_trial(cfg, trial_table(cfg, state))
    return 0


def subtract(base: Net, holes: List[Net]) -> List[Net]:
    """base minus union(holes), returned as a sorted list of maximal prefixes."""
    remaining = [base]
    for hole in holes:
        nxt = []
        for r in remaining:
            if hole.subnet_of(r) or hole == r:
                if r == hole:
                    continue
                nxt.extend(_subtract_one(r, hole))
            elif r.subnet_of(hole):
                continue  # fully removed
            else:
                nxt.append(r)
        remaining = nxt
    return sorted(remaining, key=lambda n: (int(n.network_address), n.prefixlen))


def _subtract_one(r: Net, hole: Net) -> List[Net]:
    if r == hole:
        return []
    depth = r.prefixlen + 1
    while depth <= hole.prefixlen:
        left, right = list(r.subnets(new_prefix=depth))
        if hole.subnet_of(left):
            return [right] + _subtract_one(left, hole)
        if hole.subnet_of(right):
            return [left] + _subtract_one(right, hole)
        depth += 1
    # disjoint
    return [r]


def cmd_refresh_return(args):
    """How much of a returning range can still be adopted, given local corrections."""
    cfg = load_cfg()
    state = load_state()
    base = cfg.retn
    # local corrections = A routes strictly longer than base that special-case it
    local = [r.prefix for r in state.routes
             if r.part == "A" and r.prefix.prefixlen > base.prefixlen
             and r.prefix.subnet_of(base)]
    usable = subtract(base, local)
    usable_addrs = sum(int(n.num_addresses) for n in usable)
    print(f"returning range: {base}  ({base.num_addresses} addresses)")
    print(f"local corrections shadowing it: {[str(n) for n in sorted(set(local))] or '(none)'}")
    print(f"usable after corrections: {[str(n) for n in usable]}  "
          f"({usable_addrs}/{base.num_addresses} addresses = "
          f"{100 * usable_addrs // int(base.num_addresses)}%)")
    shadowed_addrs = int(base.num_addresses) - usable_addrs
    print(f"shadowed / unusable: {shadowed_addrs} addresses")
    if not args.dry_run:
        # adopt: add return rows for each usable piece pointing back through core
        existing = {(r.part, str(r.prefix), r.ifc, r.nbr) for r in state.routes}
        for n in usable:
            key = ("A", str(n), 0, "coreA")
            if key not in existing:
                state.routes.append(Route("A", n, 0, "coreA", 150, "return", 2))
        save_state(state)
        print("adopted usable return prefixes via transit (ttl 2)")
    return 0


def cmd_alias(args):
    """Back up a landing route; optionally remove it for a trial, or restore it."""
    cfg = load_cfg()
    state = load_state()
    backup_path = "landing.backup.json"
    if args.backup:
        landings = [r.to_json() for r in state.routes
                    if r.part == "A" and r.ifc in cfg.allowed_ifcs and r.prefix.prefixlen == 32]
        save_json(backup_path, landings)
        print(f"backed up {len(landings)} landing routes -> {backup_path}")
        return 0
    if args.remove is not None:
        ifc = int(args.remove)
        idx = None
        for i, r in enumerate(state.routes):
            if r.part == "A" and r.ifc == ifc and r.prefix.prefixlen == 32:
                if args.prefix is None or str(r.prefix) == args.prefix:
                    idx = i
                    break
        if idx is None:
            raise ToolError(f"no landing route on ifc {ifc}"
                            + (f" with prefix {args.prefix}" if args.prefix else ""))
        removed_route = state.routes[idx]
        before = trial_table(cfg, state)
        new = state.copy()
        new.routes = [r for i, r in enumerate(state.routes) if i != idx]
        after = trial_table(cfg, new)
        save_state(new)
        aff = [{"part": a["part"], "addr": a["addr"],
                "before": f"{b['prefix']} ifc{b['ifc']}",
                "after": f"{a['prefix']} ifc{a['ifc']}"}
               for b, a in zip(before, after)
               if (b["prefix"], b["ifc"]) != (a["prefix"], a["ifc"])]
        print(f"withdrew ONE backup route: {removed_route.prefix} on ifc {ifc}")
        print(f"newly affected flows: {len(aff)}")
        for a in aff:
            print(f"  {a['part']}:{a['addr']:16} {a['before']:20} -> {a['after']}")
        record_review(ApplyReport(accepted=True, epoch=state.epoch, change=args.change,
                                  affected_probes=aff),
                      f"alias withdraw one backup {removed_route.prefix} on ifc {ifc}",
                      "accepted", extra={"trials": after})
        return 0
    if args.restore:
        try:
            landings = load_json(backup_path)
        except FileNotFoundError:
            raise ToolError("no landing backup; run alias --backup first")
        existing = {(r.part, str(r.prefix), r.ifc, r.nbr) for r in state.routes}
        added = 0
        for d in landings:
            r = Route.from_json(d)
            if (r.part, str(r.prefix), r.ifc, r.nbr) not in existing:
                state.routes.append(r)
                added += 1
        save_state(state)
        print(f"restored {added} landing route(s) from backup")
        print_trial(cfg, trial_table(cfg, state))
        return 0
    raise ToolError("alias needs one of --backup / --remove / --restore")


def cmd_unhide(args):
    """Reveal a soft-denied / out-of-domain range into the domain (adds to design)."""
    cfg = load_cfg()
    state = load_state()
    try:
        net = Net(args.add)
    except ValueError:
        raise ToolError(f"bad prefix {args.add!r}")
    if not net.subnet_of(cfg.space):
        raise ToolError(f"{net} is not within the exchange space {cfg.space}")
    # must currently be un-routed in A (outside the domain's routing)
    hit = any(r.part == "A" and net.subnet_of(r.prefix) for r in state.routes)
    if hit:
        print(f"{net} already routed in A; nothing to un-hide", file=sys.stderr)
        return 2
    state.routes.append(Route("A", net, cfg.landing_names["direct"], "", 250, ACTIVE))
    save_state(state)
    print(f"un-hid {net} into partition A as direct")
    print_trial(cfg, trial_table(cfg, state))
    return 0


def cmd_review(args):
    try:
        lines = [json.loads(l) for l in read_text(REVIEW).splitlines() if l.strip()]
    except FileNotFoundError:
        raise ToolError("no review log yet")
    if args.change:
        lines = [r for r in lines if r["change"] == args.change]
    if not lines:
        print(f"no record for change {args.change}")
        return 2
    for rec in lines:
        print(f"change {rec['change']} [{rec['decision']}] {rec['summary']} "
              f"(epoch {rec['epoch']})")
        for e in rec.get("real_edits", []):
            print(f"  real: {e['fake']} -> {e['real']} ifc {e['real_ifc']}")
        for reason in rec.get("reasons", []):
            print(f"  reason: {reason}")
        for a in rec.get("affected", []):
            print(f"  affected {a['part']}:{a['addr']} {a.get('before')} -> {a.get('after')}")
        if args.trials:
            print_trial_from_records(rec.get("trials", []))
    return 0


def print_trial_from_records(table):
    print(f"  {'part':5}{'addr':16}{'prefix':18}{'ifc':>5}{'via':>10}")
    for t in table:
        print(f"  {t['part']:5}{t['addr']:16}{str(t['prefix']):18}"
              f"{str(t['ifc']):>5}{t['via']:>10}")


# --------------------------------------------------------------------------- #
#  End-to-end walkthrough
# --------------------------------------------------------------------------- #
def cmd_walkthrough(args):
    quiet = argparse.Namespace(**{**vars(args)})
    def run(fn, **kw):
        ns = argparse.Namespace(**kw)
        return fn(ns)

    print("=" * 80); print("STEP 0  init: real world + anonymized design"); print("=" * 80)
    try:
        open(REVIEW, "w").close()   # clean review log for a reproducible run
    except OSError:
        pass
    run(cmd_init, config=None)
    cfg = load_cfg()
    print("\n-- what the contractor sees (package.anon) --")
    print(read_text(PKG), end="")

    print("\n" + "=" * 80)
    print("STEP 1  leak check of the anonymous package")
    print("=" * 80)
    run(cmd_check, file=PKG)

    print("\n" + "=" * 80)
    print("STEP 2  anon-group view: show behavioural groups")
    print("=" * 80)
    run(cmd_groups, anon=True)

    print("\n" + "=" * 80)
    print("STEP 3  DIVERT one more-specific segment in the trusted partition")
    print("=" * 80)
    run(cmd_divert, segment="198.18.0.0/30", to="L2", change=None)

    print("\n-- full trial after diversion --")
    state = load_state(); print_trial(cfg, trial_table(cfg, state))
    a_sib = lpm(state.routes, "A", "10.0.0.2")
    b_sib = lpm(state.routes, "B", "10.0.0.2")
    print(f"\nCHECK sibling same-number segment: A 10.0.0.2 -> ifc {a_sib.ifc}; "
          f"B 10.0.0.2 -> ifc {b_sib.ifc} (B must stay 30)")

    print("\n" + "=" * 80)
    print("STEP 4  AMBIGUITY: the same edit seen across BOTH partitions")
    print("=" * 80)
    fake = Net("198.18.0.0/30"); real = unmirror(cfg, fake)
    both = candidates(cfg, real)
    print(f"fake {fake} -> real {real}; candidates across A,B: "
          f"{[f'{c.part}:ifc{c.ifc}' for c in both]} -> "
          f"{'AMBIGUOUS if partitions merged' if len(both) > 1 else 'unique'}")
    print("scoped apply fixes the partition to A, so it is expressible unambiguously.")

    print("\n" + "=" * 80)
    print("STEP 5  UNREPRESENTABLE edit is rejected whole (no partial apply)")
    print("=" * 80)
    state = load_state()
    bad = build_package(cfg, state)
    bad.append({"prefix": "198.18.0.8/30", "ifc": 1, "nbr": 0, "pri": 100, "tag": ACTIVE})
    ns_, rep = apply_package(cfg, state, bad, change="C-bad")
    print("accepted:" , rep.accepted)
    for r in rep.reasons:
        print("  reason:", r)
    print("epoch unchanged:", ns_.epoch == state.epoch)

    print("\n" + "=" * 80)
    print("STEP 6  ONLY a priority relation changes -> a prior return packet")
    print("        loses the object it was originally sent against")
    print("=" * 80)
    st = load_state()
    orig12 = lpm(st.routes, "A", "10.0.0.12")
    print(f"before: 10.0.0.12 -> {orig12.prefix} ifc{orig12.ifc} "
          f"nbr={orig12.nbr or '-'} pri={orig12.pri} tag={orig12.tag}")
    print("  this is the route a return packet was sent against")
    run(cmd_reprioritize, prefix="10.0.0.8/29", ifc=25, pri=300)
    st = load_state()
    now12 = lpm(st.routes, "A", "10.0.0.12")
    print(f"after : 10.0.0.12 -> {now12.prefix} ifc{now12.ifc} "
          f"nbr={now12.nbr or '-'} pri={now12.pri} tag={now12.tag}")
    print("  the return route still exists, but the in-flight return packet's "
          "applicable object was superseded by the priority change")
    print("  (only priorities were edited; no prefix was added or removed)")

    print("\n" + "=" * 80)
    print("STEP 7  refresh-return: how much survives a local correction")
    print("=" * 80)
    run(cmd_init, config=None)  # fresh so corrections are deterministic
    st = load_state()
    st.routes.append(Route("A", Net("10.0.0.12/31"), 11, "", 10, ACTIVE))  # local correction
    save_state(st)
    run(cmd_refresh_return, dry_run=True)

    print("\n" + "=" * 80)
    print("STEP 8  restore original; withdraw ONE landing backup; re-trial")
    print("=" * 80)
    run(cmd_restore, **{})
    run(cmd_alias, backup=True, remove=None, prefix=None, restore=False, change="L1")
    run(cmd_alias, backup=False, remove="20", prefix="10.0.0.6/32", restore=False,
        change="L2")

    print("\n" + "=" * 80)
    print("STEP 9  reviewable per-change trials")
    print("=" * 80)
    try:
        recs = [json.loads(l) for l in read_text(REVIEW).splitlines() if l.strip()]
        for rec in recs:
            print(f"  {rec['change']:8} {rec['decision']:9} {rec['summary']}")
    except FileNotFoundError:
        pass

    print("\nArtifacts: package.anon, package.manifest.json, real.json, original.json,")
    print("           review.jsonl  (query a change with `dntool.py review --change C1`)")
    return 0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init"); sp.add_argument("--config"); sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("package"); sp.add_argument("--out"); sp.set_defaults(fn=cmd_package)
    sp = sub.add_parser("check"); sp.add_argument("file"); sp.set_defaults(fn=cmd_check)
    sp = sub.add_parser("groups"); sp.add_argument("--anon", action="store_true"); sp.set_defaults(fn=cmd_groups)
    sp = sub.add_parser("trial"); sp.set_defaults(fn=cmd_trial)
    sp = sub.add_parser("apply"); sp.add_argument("--design"); sp.add_argument("--change")
    sp.set_defaults(fn=cmd_apply)
    sp = sub.add_parser("route"); sp.add_argument("--group", required=True)
    sp.add_argument("--to", required=True); sp.add_argument("--change"); sp.set_defaults(fn=cmd_route)
    sp = sub.add_parser("divert"); sp.add_argument("--segment", required=True)
    sp.add_argument("--to", required=True); sp.add_argument("--change"); sp.set_defaults(fn=cmd_divert)
    sp = sub.add_parser("round"); sp.set_defaults(fn=cmd_round)
    sp = sub.add_parser("restore"); sp.set_defaults(fn=cmd_restore)
    sp = sub.add_parser("reprioritize"); sp.add_argument("--prefix", required=True)
    sp.add_argument("--ifc", required=True); sp.add_argument("--pri", required=True)
    sp.set_defaults(fn=cmd_reprioritize)
    sp = sub.add_parser("refresh-return"); sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_refresh_return)
    sp = sub.add_parser("alias"); sp.add_argument("--backup", action="store_true")
    sp.add_argument("--remove"); sp.add_argument("--prefix"); sp.add_argument("--restore", action="store_true")
    sp.add_argument("--change", default="L0"); sp.set_defaults(fn=cmd_alias)
    sp = sub.add_parser("unhide"); sp.add_argument("--add", required=True); sp.set_defaults(fn=cmd_unhide)
    sp = sub.add_parser("review"); sp.add_argument("--change"); sp.add_argument("--trials", action="store_true")
    sp.set_defaults(fn=cmd_review)
    sp = sub.add_parser("walkthrough"); sp.set_defaults(fn=cmd_walkthrough)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
