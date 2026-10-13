#!/usr/bin/env python3
"""Build references/xml-api/snapshot/ from a scrubbed object-definition dump (maintenance only).

    python -I scripts/build_object_snapshot.py --dump <folder written by dump_object_definitions.py> \
        --out references/xml-api/snapshot --release "2026 Release 3" --verified YYYY-MM-DD [--max-bytes 1000000]

The dump holds `inspect.xml` (the object list), `lookup/<OBJECT>.xml` (one `<data>` element per object, the
`lookup` result) and `manifest.json`. The builder reads the documented `lookup` shape
(`Type/Fields/Field/ID|LABEL|DESCRIPTION|REQUIRED|READONLY|DATATYPE|ISCUSTOM|VALIDVALUES/VALIDVALUE` and
`Relationships/Relationship/OBJECTPATH|OBJECTNAME|LABEL|RELATIONSHIPTYPE|RELATEDBY`), tolerating case differences,
and writes:
  INDEX.md        provenance, counts, lookup recipes
  objects.md      one row per object: name, label (from inspect), fields, relationships, bundle file and line
  fields-NN.md    bundles of about --max-bytes: per object a `## OBJECT` heading, the field table and the
                  relationship table
It refuses to write if any field still carries ISCUSTOM true (the dump must be scrubbed first) and prints the
counts for references/xml-api/INDEX.md. Written against the documented shape before a real dump existed: check
the first real output by hand (MAINTENANCE.md).
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET


def child_text(el, name):
    for c in el:
        if c.tag.lower() == name.lower():
            return (c.text or "").strip()
    return ""


def find_all_ci(el, name):
    return [c for c in el.iter() if c.tag.lower() == name.lower()]


def cell(s):
    return re.sub(r"\s+", " ", s or "").replace("|", "\\|").strip()


def parse_lookup(path):
    data = ET.parse(path).getroot()
    fields, rels = [], []
    for f in find_all_ci(data, "Field"):
        valid = [cell(v.text) for v in find_all_ci(f, "VALIDVALUE") if (v.text or "").strip()]
        fields.append({
            "id": child_text(f, "ID"), "label": child_text(f, "LABEL"), "description": child_text(f, "DESCRIPTION"),
            "required": child_text(f, "REQUIRED"), "readonly": child_text(f, "READONLY"),
            "datatype": child_text(f, "DATATYPE"), "iscustom": child_text(f, "ISCUSTOM"), "valid": valid,
        })
    for r in find_all_ci(data, "Relationship"):
        rels.append({
            "path": child_text(r, "OBJECTPATH"), "object": child_text(r, "OBJECTNAME"), "label": child_text(r, "LABEL"),
            "type": child_text(r, "RELATIONSHIPTYPE"), "by": child_text(r, "RELATEDBY"),
        })
    return fields, rels


def parse_inspect(path):
    """{name: label} from the inspect result, whatever the exact element shape."""
    labels = {}
    if not os.path.exists(path):
        return labels
    data = ET.parse(path).getroot()
    for el in data.iter():
        n = el.attrib.get("typename") or el.attrib.get("Name") or el.attrib.get("name")
        if n:
            labels[n.strip()] = (el.text or "").strip() or el.attrib.get("label", "")
        elif el.tag.lower() in ("type", "object") and (el.text or "").strip() and len(el) == 0:
            labels[el.text.strip()] = ""
    return labels


def render_object(name, label, fields, rels):
    out = [f'<a id="{name.lower()}"></a>', f"## {name}" + (f" ({label})" if label else ""), "",
           f"{len(fields)} fields, {len(rels)} relationships.", ""]
    if fields:
        out.append("| ID | Label | Type | Required | Read-only | Valid values | Description |")
        out.append("|---|---|---|---|---|---|---|")
        for f in fields:
            out.append(f"| `{cell(f['id'])}` | {cell(f['label'])} | {cell(f['datatype'])} | {cell(f['required'])} | "
                       f"{cell(f['readonly'])} | {', '.join('`' + v + '`' for v in f['valid'])} | {cell(f['description'])} |")
        out.append("")
    if rels:
        out.append("| Object path | Related object | Type | Related by | Label |")
        out.append("|---|---|---|---|---|")
        for r in rels:
            out.append(f"| `{cell(r['path'])}` | `{cell(r['object'])}` | {cell(r['type'])} | `{cell(r['by'])}` | {cell(r['label'])} |")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--release", required=True)
    ap.add_argument("--verified", required=True)
    ap.add_argument("--max-bytes", type=int, default=1_000_000)
    args = ap.parse_args()

    manifest = {}
    mp = os.path.join(args.dump, "manifest.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            manifest = json.load(f)
    labels = parse_inspect(os.path.join(args.dump, "inspect.xml"))
    lookup_dir = os.path.join(args.dump, "lookup")
    names = sorted(n[:-4] for n in os.listdir(lookup_dir) if n.endswith(".xml"))
    if not names:
        sys.exit("no lookup/*.xml in the dump")

    entries, custom_left, total_fields, total_rels = [], [], 0, 0
    for n in names:
        fields, rels = parse_lookup(os.path.join(lookup_dir, n + ".xml"))
        custom_left += [f"{n}.{f['id']}" for f in fields if f["iscustom"].lower() in ("true", "1", "t")]
        total_fields += len(fields)
        total_rels += len(rels)
        entries.append((n, labels.get(n, ""), fields, rels, render_object(n, labels.get(n, ""), fields, rels)))
    if custom_left:
        sys.exit("not written: custom fields survive the scrub: " + ", ".join(custom_left[:10]) + (" ..." if len(custom_left) > 10 else ""))

    prov = (f"<!-- source: lookup and inspect results of a clean Sage Intacct developer company (scrubbed dump in "
            f"sources/sageintacct-assistant/object-definitions-{re.sub(r'[^A-Za-z0-9]+', '-', args.release).strip('-')}/), "
            f"generated by scripts/build_object_snapshot.py | version: Sage Intacct {args.release}, XML Web Services DTD 3.0 | "
            f"verified: {args.verified} -->")
    os.makedirs(args.out, exist_ok=True)
    for old in os.listdir(args.out):
        if old.endswith(".md"):
            os.remove(os.path.join(args.out, old))

    # bundles
    head = prov + "\n\n# Object definitions snapshot: fields and relationships\n\nOne `##` block per object, as `lookup` reported it. Grep `## APBILL` or the field ID.\n\n"
    bundles, cur, size = [], [], 0
    for e in entries:
        b = len(e[4].encode("utf-8")) + 2
        if cur and size + b > args.max_bytes:
            bundles.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += b
    if cur:
        bundles.append(cur)
    where = {}
    for i, bundle in enumerate(bundles, 1):
        fname = f"fields-{i:02d}.md"
        line = head.count("\n") + 1
        parts = [head]
        for e in bundle:
            text = e[4].rstrip("\n")
            where[e[0]] = (fname, line, text.count("\n") + 1)
            parts.append(text + "\n\n")
            line += text.count("\n") + 2
        with open(os.path.join(args.out, fname), "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(parts).rstrip("\n") + "\n")

    # objects.md
    rows = [prov, "", f"# Object definitions snapshot: objects ({args.release})", "",
            f"{len(entries)} objects, {total_fields} fields, {total_rels} relationships, as reported by `inspect` and `lookup` on a clean "
            f"developer company on {args.release}. `File` / `Line` / `Lines` locate the object's block in the bundles: open exactly that range.", "",
            "| Object | Label | Fields | Relationships | File | Line | Lines |", "|---|---|---|---|---|---|---|"]
    for n, label, fields, rels, _ in entries:
        fname, line, n_lines = where[n]
        rows.append(f"| `{n}` | {cell(label)} | {len(fields)} | {len(rels)} | [{fname}]({fname}#{n.lower()}) | {line} | {n_lines} |")
    with open(os.path.join(args.out, "objects.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(rows) + "\n")

    # INDEX.md
    idx = [prov, "", f"# Object definitions snapshot index ({args.release})", "",
           f"Exact object, field, valid-value and relationship names of a **clean** Sage Intacct developer company on **{args.release}**, "
           f"dumped on {manifest.get('dumped', 'date not recorded')} with `scripts/dump_object_definitions.py` and compiled by "
           "`scripts/build_object_snapshot.py`. Use it for exact names and valid values; use the portal tables in `../objects/` for "
           "meanings, examples and release history. Custom fields and custom objects of the source company were removed before the "
           "dump was committed.", "",
           "| Path | Covers |", "|---|---|",
           f"| `objects.md` | one row per object ({len(entries)}): label, field and relationship counts, bundle file and line |"]
    for i, bundle in enumerate(bundles, 1):
        idx.append(f"| `fields-{i:02d}.md` | `{bundle[0][0]}` to `{bundle[-1][0]}` ({len(bundle)} objects) |")
    idx += ["", "## Looking things up", "",
            "| Question | Where |", "|---|---|",
            "| Does object X exist, how many fields | `objects.md`: grep `` `X` `` |",
            "| The fields of X with type, required, read-only and valid values | the `File` / `Line` / `Lines` of X in `objects.md`, then read that range; or grep `## X` in the bundles |",
            "| Which objects have a field F | grep `` `F` `` across the bundles |",
            "| How X relates to Y (the `OBJECTPATH` for `query` dot notation) | X's relationships table |", "",
            "## Known limits", "",
            "- `lookup` reports queryable fields and relationships; owned objects (transaction lines) are separate objects, and nothing here "
            "says which function creates or updates what: that is the portal reference (`../objects/`).",
            "- One company, one release, standard objects only; custom fields, user-defined dimensions and transaction-definition fields "
            "(`docparid`) are not included.",
            f"- Dump manifest: {len(manifest.get('lookups_failed', {}))} objects failed `lookup` and are absent; "
            f"{len(manifest.get('custom_objects_removed', []))} custom objects and "
            f"{sum(len(v) for v in manifest.get('custom_fields_removed', {}).values())} custom fields were removed."]
    with open(os.path.join(args.out, "INDEX.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(idx) + "\n")
    print(f"objects={len(entries)} fields={total_fields} relationships={total_rels} bundles={len(bundles)} -> {args.out}")


if __name__ == "__main__":
    main()
