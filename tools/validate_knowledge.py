#!/usr/bin/env python3
"""Validate the Auditra knowledge banks (vocabularies + equipment bank).

Usage:
    python tools/validate_knowledge.py            # validate, exit code 1 on any error
    python tools/validate_knowledge.py --report docs/equipment_coverage.md
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "knowledge" / "vocab"
EQUIP = ROOT / "knowledge" / "equipment"
MIN_RECORDS = 150  # first-package target (Methodology.doc, first executable package, output 1)

REQUIRED = ["id", "fa", "en", "sub", "role", "params", "note"]
OPTIONAL = ["in", "out", "flows", "ind", "lvl", "ref", "status", "links"]
ID_RE = re.compile(r"^EQP-(\d{2})-(\d{3})$")
SUB_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def load(name):
    return yaml.safe_load((VOCAB / name).read_text(encoding="utf-8"))


def ids(name):
    return {row["id"] for row in load(name)}


def main():
    errors = []
    v = {
        "systems": ids("systems.yaml"),
        "industries": ids("industries.yaml"),
        "streams": ids("stream_types.yaml"),
        "flows": ids("other_flows.yaml"),
        "roles": ids("roles.yaml"),
        "levels": ids("accounting_levels.yaml"),
        "statuses": ids("review_statuses.yaml"),
        "sources": ids("sources.yaml"),
        "params": ids("parameters.yaml"),
    }
    # vocabulary self-checks
    for name in ["systems", "industries", "streams", "flows", "roles", "levels", "statuses", "sources", "params"]:
        fname = {"streams": "stream_types", "flows": "other_flows", "levels": "accounting_levels",
                 "statuses": "review_statuses", "params": "parameters"}.get(name, name) + ".yaml"
        rows = load(fname)
        if len(rows) != len(v[name]):
            errors.append(f"vocab {fname}: duplicate ids")
    if len(v["streams"]) != 15:
        errors.append(f"stream_types must hold the 15 agreed types, found {len(v['streams'])}")
    if len(v["systems"]) != 12 or len(v["industries"]) != 15:
        errors.append("systems must be SYS-01..12 and industries IND-01..15")

    records = []
    seen_ids, seen_en, seen_fa = {}, {}, {}
    files = sorted(EQUIP.glob("SYS-*.yaml"))
    if not files:
        errors.append("no equipment files found")
    for f in files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        system = doc.get("system")
        if system not in v["systems"]:
            errors.append(f"{f.name}: unknown system {system}")
            continue
        if not f.name.startswith(system):
            errors.append(f"{f.name}: file name must start with its system code {system}")
        defaults = doc.get("defaults", {})
        for rec in doc.get("records", []):
            r = {**defaults, **rec}
            rid = r.get("id", "<no id>")
            where = f"{f.name}:{rid}"
            for k in REQUIRED:
                if not r.get(k):
                    errors.append(f"{where}: missing required field '{k}'")
            for k in r:
                if k not in REQUIRED + OPTIONAL:
                    errors.append(f"{where}: unknown field '{k}'")
            m = ID_RE.match(str(rid))
            if not m:
                errors.append(f"{where}: id must look like EQP-01-001")
            else:
                if f"SYS-{m.group(1)}" != system:
                    errors.append(f"{where}: id prefix does not match system {system}")
            if rid in seen_ids:
                errors.append(f"{where}: duplicate id (also in {seen_ids[rid]})")
            seen_ids[rid] = f.name
            for key, seen in (("en", seen_en), ("fa", seen_fa)):
                val = (r.get(key) or "").strip().lower()
                if val in seen:
                    errors.append(f"{where}: duplicate {key} name '{r.get(key)}' (also {seen[val]})")
                seen[val] = rid
            if r.get("sub") and not SUB_RE.match(r["sub"]):
                errors.append(f"{where}: sub must be snake_case")

            def check_list(key, allowed, required=False):
                vals = r.get(key, [])
                if not isinstance(vals, list):
                    errors.append(f"{where}: '{key}' must be a list")
                    return []
                if required and not vals:
                    errors.append(f"{where}: '{key}' must not be empty")
                bad = [x for x in vals if x not in allowed]
                if bad:
                    errors.append(f"{where}: unknown {key} values {bad}")
                if len(vals) != len(set(vals)):
                    errors.append(f"{where}: duplicate values in '{key}'")
                return vals

            check_list("role", v["roles"], required=True)
            check_list("params", v["params"], required=True)
            ins = check_list("in", v["streams"])
            outs = check_list("out", v["streams"])
            flows = check_list("flows", v["flows"])
            check_list("lvl", v["levels"], required=True)
            check_list("ref", v["sources"])
            if not (ins or outs or flows):
                errors.append(f"{where}: at least one of in/out/flows must be non-empty")
            ind = r.get("ind", ["ALL"])
            if ind != ["ALL"]:
                check_list("ind", v["industries"], required=True)
                if "ALL" in ind:
                    errors.append(f"{where}: 'ALL' cannot be mixed with industry codes")
            if r.get("status") not in v["statuses"]:
                errors.append(f"{where}: unknown status {r.get('status')}")
            if r.get("status") in ("expert_reviewed", "approved") and not r.get("ref"):
                errors.append(f"{where}: reviewed/approved records must cite a source")
            # physical sanity: a treatment role must transform something
            if "treatment" in r.get("role", []) and not (ins and (outs or flows)):
                errors.append(f"{where}: treatment equipment needs inputs and outputs")
            if "evaporation" in ins:
                errors.append(f"{where}: evaporation can only be an output")
            for s in ("raw_water",):
                pass
            r["_system"] = system
            r["_file"] = f.name
            records.append(r)

    # coverage checks
    per_system = Counter(r["_system"] for r in records)
    for s in sorted(v["systems"]):
        if per_system[s] == 0:
            errors.append(f"system {s} has no records")
    if len(records) < MIN_RECORDS:
        errors.append(f"only {len(records)} records, target is at least {MIN_RECORDS}")
    ind_cov = Counter()
    for r in records:
        inds = v["industries"] if r.get("ind", ["ALL"]) == ["ALL"] else r["ind"]
        for i in inds:
            ind_cov[i] += 1
    stream_use = Counter()
    for r in records:
        for k in ("in", "out"):
            for s in r.get(k, []):
                stream_use[s] += 1
    for s in sorted(v["streams"]):
        if stream_use[s] == 0:
            errors.append(f"stream type {s} is never used by any equipment record")
    used_params = {p for r in records for p in r["params"]}
    unused_params = sorted(v["params"] - used_params)
    refs_used = Counter(x for r in records for x in r.get("ref", []))

    print(f"records: {len(records)} | systems: {len(per_system)} | errors: {len(errors)}")
    for s in sorted(per_system):
        print(f"  {s}: {per_system[s]}")
    if unused_params:
        print("  parameters not used by any record:", ", ".join(unused_params))
    if errors:
        print("\nERRORS")
        for e in errors:
            print(" -", e)

    if "--report" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--report") + 1])
        sysnames = {row["id"]: row for row in load("systems.yaml")}
        indnames = {row["id"]: row for row in load("industries.yaml")}
        lines = ["# Equipment bank coverage (generated)", "",
                 f"Total records: **{len(records)}** (first-package target: at least {MIN_RECORDS}).",
                 "All records are `draft`: none has been reviewed by an expert.", "",
                 "## By system", "", "| System | Name | Records |", "|---|---|---|"]
        for s in sorted(per_system):
            lines.append(f"| {s} | {sysnames[s]['en']} / {sysnames[s]['fa']} | {per_system[s]} |")
        lines += ["", "## By industry group (records that apply; ALL-industry records count for every group)", "",
                  "| Group | Name | Records |", "|---|---|---|"]
        for i in sorted(indnames):
            lines.append(f"| {i} | {indnames[i]['en']} / {indnames[i]['fa']} | {ind_cov[i]} |")
        lines += ["", "## Records that cite a source", "",
                  f"{sum(1 for r in records if r.get('ref'))} of {len(records)} records cite Methodology.doc sections; "
                  "the rest carry no source yet and must be sourced during review.", "",
                  "## Stream-type usage", "", "| Stream type | Used in (in+out) |", "|---|---|"]
        for s in sorted(v["streams"]):
            lines.append(f"| {s} | {stream_use[s]} |")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("report written to", out)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
