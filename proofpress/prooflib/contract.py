"""Binding contracts （装订合约） and their feasibility against a manifest.

A contract describes the print shop's binding equipment:

* paper tray (paper_w x paper_h) and margin,
* flip_axis -- the axis the paper is turned around between sides,
* folds -- at least two sequential folds (axis / moving half / over-under),
* cuts -- which folded edges are trimmed open （开裁）,
* inserts -- where insert leaves go (which signature, after which leaf),
* optional per-signature overrides (e.g. a different flip axis for one
  signature only).

The spine is *derived*: the single closed fold edge that is not cut away.
Feasibility checks make sure the contract can physically realize the signed
manifest (leaf counts, insert placement, opening/spread adjacency, spine on
the side demanded by the binding direction).
"""

from . import geom


def closed_edges_for(folds):
    """Closed packet edges after all folds (world directions)."""
    W, H = geom.grid_dims_from_folds(folds)
    grid = geom.make_flat_grid(W, H, "vertical")
    _stack, closed = geom.fold_sheet(grid, W, H, folds)
    return closed


def spine_of(contract):
    closed = closed_edges_for(contract["folds"])
    spine = closed - set(contract.get("cuts", []))
    if len(spine) != 1:
        raise ValueError(
            "contract %s: closed edges %s minus cuts %s leave %d spine "
            "candidates" % (contract["id"], sorted(closed),
                            sorted(contract.get("cuts", [])), len(spine))
        )
    return next(iter(spine))


def reader_rotation_for(contract, binding_direction):
    """The in-plane rotation (0 or 180 deg) the reader applies to the
    finished booklet so the cover is upright, chosen such that the spine
    then sits on the side the binding direction demands (ltr: left/W,
    rtl: right/E).  Raises ValueError if the contract cannot serve it."""
    spine = spine_of(contract)
    want = "W" if binding_direction == "ltr" else "E"
    for rot in (0, 180):
        view = spine if rot == 0 else geom.rot_dir_180(spine)
        if view == want:
            return rot
    raise ValueError(
        "contract %s: spine ends on %s, cannot serve %s binding"
        % (contract["id"], spine, binding_direction)
    )


def contract_for_signature(contract, sig_id):
    """Contract with per-signature overrides applied."""
    ov = contract.get("signature_overrides", {}).get(str(sig_id), {})
    if not ov:
        return contract
    merged = dict(contract)
    merged.update(ov)
    merged["signature_overrides"] = {}
    return merged


def check_feasible(contract, manifest):
    """Raise ValueError unless the contract can realize the manifest."""
    W, H = geom.grid_dims_from_folds(contract["folds"])
    leaves_per_sig = W * H
    if len(contract["folds"]) < 2:
        raise ValueError("contract needs at least two sequential folds")
    # every signature fits the fold equipment
    for sig in manifest["signatures"]:
        main = sig["positions"]
        if len(main) != 2 * leaves_per_sig:
            raise ValueError(
                "signature %s: %d main pages but the folds make %d leaves "
                "(%d pages)" % (sig["id"], len(main), leaves_per_sig,
                                2 * leaves_per_sig)
            )
        c_ins = [i for i in contract.get("inserts", [])
                 if i["signature"] == sig["id"]]
        m_ins = sig.get("inserts", [])
        if len(c_ins) != len(m_ins):
            raise ValueError(
                "signature %s: contract offers %d insert slots, manifest "
                "needs %d" % (sig["id"], len(c_ins), len(m_ins))
            )
        for ci, mi in zip(sorted(c_ins, key=lambda i: i["after_leaf"]),
                          sorted(m_ins, key=lambda i: i["after_leaf"])):
            if ci["after_leaf"] != mi["after_leaf"]:
                raise ValueError(
                    "signature %s: insert placement mismatch (contract "
                    "after leaf %d, manifest after leaf %d)"
                    % (sig["id"], ci["after_leaf"], mi["after_leaf"])
                )
            if 2 * ci.get("count", 1) != len(mi["positions"]):
                raise ValueError("insert leaf count mismatch")
            if not (1 <= mi["after_leaf"] <= len(main) // 2):
                raise ValueError("insert placement out of range")
    # opening / spread approvals must land on real openings
    from .manifest import opening_pairs, signature_sequence

    pairs = set()
    for l, r in opening_pairs(manifest):
        pairs.add((l, r))
        pairs.add((r, l))
    sig_seqs = {s["id"]: signature_sequence(manifest, s["id"])
                for s in manifest["signatures"]}
    for ap in manifest.get("approvals", []):
        if ap["scope"] == "opening":
            if (ap["left"], ap["right"]) not in pairs:
                raise ValueError(
                    "opening approval (%d,%d) is not a real opening under "
                    "this contract" % (ap["left"], ap["right"])
                )
            # both pages must live in the same signature (one folded sheet)
            if not any(ap["left"] in seq and ap["right"] in seq
                       for seq in sig_seqs.values()):
                raise ValueError(
                    "opening approval (%d,%d) spans signatures; cannot be "
                    "imposed on one sheet" % (ap["left"], ap["right"])
                )
    # spine side vs binding direction (raises if impossible)
    reader_rotation_for(contract, manifest["binding_direction"])
    return True
