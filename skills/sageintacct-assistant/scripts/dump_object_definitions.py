#!/usr/bin/env python3
"""Dump the object definitions of a Sage Intacct company through the XML API, scrubbed (maintenance only).

    python scripts/dump_object_definitions.py --out <folder outside the repo> --release "2026 Release 3" [--objects APBILL,VENDOR]
                                              [--batch 10] [--delay 0.5] [--dry-run]

Run this yourself against a **clean developer or implementation company**; never against a customer's. Credentials
are read from environment variables only, are never printed, and never reach the output:

    INTACCT_SENDER_ID, INTACCT_SENDER_PASSWORD      Web Services sender credentials
    INTACCT_COMPANY_ID, INTACCT_USER_ID, INTACCT_USER_PASSWORD   company login
    INTACCT_LOCATION_ID   optional: entity to sign in to (multi-entity shared companies)
    INTACCT_ENDPOINT      optional: gateway URL, default https://api.intacct.com/ia/xml/xmlgw.phtml

What it does (stdlib only, Python 3.10+):
  1. `getAPISession` with the login credentials; later calls use the returned session ID and endpoint.
  2. `inspect` with `<object>*</object>`: the list of objects the company exposes.
  3. `lookup` per object (`--batch` functions per request): fields (ID, label, description, required, read-only,
     data type, custom flag, valid values) and relationships, exactly as the API reports them.

What it writes to --out (and nothing else):
  inspect.xml            the `<data>` of the inspect result, custom objects removed
  lookup/<OBJECT>.xml    the `<data>` of each lookup result, custom fields (ISCUSTOM true) removed
  dropped.txt            objects and fields that were removed, for your review
  manifest.json          release, date, counts, failures (error number and description per object), gateway host
The `<control>` block of each response (sender ID), the session ID, the endpoint URL, the company ID and the user ID
are never written; the script refuses to write any file in which one of those values appears. Objects whose name
is not upper-case letters, digits and underscores are treated as custom (Platform Services) objects and dropped.
Review `dropped.txt` and the output before sharing or committing it under `sources/sageintacct-assistant/`.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

DEFAULT_ENDPOINT = "https://api.intacct.com/ia/xml/xmlgw.phtml"
STANDARD_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


def env(name, required=True):
    v = os.environ.get(name, "")
    if required and not v:
        sys.exit(f"environment variable {name} is not set")
    return v


def request_xml(sender_id, sender_password, auth_xml, functions_xml, control_id):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<request>\n"
        "  <control>\n"
        f"    <senderid>{escape(sender_id)}</senderid>\n"
        f"    <password>{escape(sender_password)}</password>\n"
        f"    <controlid>{control_id}</controlid>\n"
        "    <uniqueid>false</uniqueid>\n"
        "    <dtdversion>3.0</dtdversion>\n"
        "    <includewhitespace>false</includewhitespace>\n"
        "  </control>\n"
        '  <operation transaction="false">\n'
        f"    <authentication>{auth_xml}</authentication>\n"
        f"    <content>\n{functions_xml}    </content>\n"
        "  </operation>\n"
        "</request>\n"
    )


def login_xml(company_id, user_id, password, location_id):
    loc = f"<locationid>{escape(location_id)}</locationid>" if location_id else ""
    return (f"<login><userid>{escape(user_id)}</userid><companyid>{escape(company_id)}</companyid>"
            f"<password>{escape(password)}</password>{loc}</login>")


def post(endpoint, body):
    req = urllib.request.Request(endpoint, data=body.encode("utf-8"),
                                 headers={"Content-Type": "application/xml", "User-Agent": "claude-skills object dump"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8")


def parse_response(text):
    """Return (control status, [result elements]) or raise with the gateway error text."""
    root = ET.fromstring(text)
    ctl = root.find("control")
    status = ctl.findtext("status") if ctl is not None else None
    err = root.find("errormessage")
    if err is not None and root.find("operation") is None:
        e = err.find("error")
        raise RuntimeError(f"gateway error {e.findtext('errorno') if e is not None else ''}: "
                           f"{e.findtext('description') if e is not None else ''} {e.findtext('description2') if e is not None else ''}")
    op = root.find("operation")
    auth = op.find("authentication") if op is not None else None
    if auth is not None and auth.findtext("status") == "failure":
        e = op.find(".//errormessage/error")
        raise RuntimeError(f"authentication failed: {e.findtext('description') if e is not None else ''} "
                           f"{e.findtext('description2') if e is not None else ''}")
    return status, (op.findall("result") if op is not None else [])


def result_error(result):
    e = result.find(".//errormessage/error")
    if e is None:
        return "unknown error"
    return f"{e.findtext('errorno')}: {e.findtext('description')} {e.findtext('description2') or ''}".strip()


def scrub_fields(data):
    """Remove every Field element whose ISCUSTOM (any case) is true; return the names removed."""
    removed = []
    for parent in data.iter():
        for child in list(parent):
            if child.tag.lower() == "field":
                flag = next((c.text for c in child if c.tag.lower() == "iscustom"), None)
                if flag is not None and flag.strip().lower() in ("true", "1", "t"):
                    fid = next((c.text for c in child if c.tag.lower() == "id"), "?")
                    removed.append(fid)
                    parent.remove(child)
    return removed


def inspect_names(data):
    """Object names from an inspect result, whatever the exact shape: an attribute typename/Name, or the element text."""
    names = []
    for el in data.iter():
        if el is data:
            continue
        n = el.attrib.get("typename") or el.attrib.get("Name") or el.attrib.get("name")
        if n:
            names.append(n.strip())
        elif el.tag.lower() in ("type", "object", "name") and el.text and el.text.strip() and len(el) == 0:
            names.append(el.text.strip())
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder, outside the repository")
    ap.add_argument("--release", default="not stated", help='the company\'s release, e.g. "2026 Release 3" (Help > About)')
    ap.add_argument("--objects", default=None, help="comma-separated object names instead of inspect *")
    ap.add_argument("--batch", type=int, default=10, help="lookup functions per request")
    ap.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    ap.add_argument("--dry-run", action="store_true", help="print the first request with placeholders and exit")
    args = ap.parse_args()

    if args.dry_run:
        print(request_xml("<SENDER_ID>", "<SENDER_PASSWORD>", login_xml("<COMPANY_ID>", "<USER_ID>", "<PASSWORD>", ""),
                          '      <function controlid="getAPISession"><getAPISession/></function>\n', "dry-run"))
        print("then: <function controlid=\"...\"><inspect><object>*</object></inspect></function>")
        print("then: <function controlid=\"...\"><lookup><object>APBILL</object></lookup></function> (batched)")
        return

    sender_id, sender_pw = env("INTACCT_SENDER_ID"), env("INTACCT_SENDER_PASSWORD")
    company_id, user_id, user_pw = env("INTACCT_COMPANY_ID"), env("INTACCT_USER_ID"), env("INTACCT_USER_PASSWORD")
    location_id = env("INTACCT_LOCATION_ID", required=False)
    endpoint = env("INTACCT_ENDPOINT", required=False) or DEFAULT_ENDPOINT
    secrets = [v for v in (sender_id, sender_pw, company_id, user_id, user_pw) if v]

    os.makedirs(os.path.join(args.out, "lookup"), exist_ok=True)

    def guarded_write(path, text):
        for s in secrets + [session_id, session_endpoint]:
            if s and s in text:
                sys.exit(f"refusing to write {os.path.basename(path)}: it contains a credential, the session ID or the endpoint")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    # 1. session
    session_id = session_endpoint = ""
    body = request_xml(sender_id, sender_pw, login_xml(company_id, user_id, user_pw, location_id),
                       '      <function controlid="getAPISession"><getAPISession/></function>\n', str(uuid.uuid4()))
    _, results = parse_response(post(endpoint, body))
    if not results or results[0].findtext("status") != "success":
        sys.exit("getAPISession failed: " + (result_error(results[0]) if results else "no result"))
    api = results[0].find("data/api")
    session_id = api.findtext("sessionid")
    session_endpoint = api.findtext("endpoint") or endpoint
    print(f"session opened; gateway host {re.sub(r'^https?://([^/]+).*$', r'\\1', session_endpoint)}")
    auth = f"<sessionid>{escape(session_id)}</sessionid>"
    time.sleep(args.delay)

    manifest = {"release": args.release, "dumped": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "gateway_host": re.sub(r"^https?://([^/]+).*$", r"\1", session_endpoint), "dtdversion": "3.0",
                "location_id_used": bool(location_id), "objects": 0, "lookups_ok": 0, "lookups_failed": {},
                "custom_fields_removed": {}, "custom_objects_removed": []}
    dropped = []

    # 2. object list
    if args.objects:
        names = [n.strip() for n in args.objects.split(",") if n.strip()]
    else:
        body = request_xml(sender_id, sender_pw, auth, '      <function controlid="inspect"><inspect><object>*</object></inspect></function>\n', str(uuid.uuid4()))
        _, results = parse_response(post(session_endpoint, body))
        if not results or results[0].findtext("status") != "success":
            sys.exit("inspect failed: " + (result_error(results[0]) if results else "no result"))
        data = results[0].find("data")
        all_names = inspect_names(data)
        names = [n for n in all_names if STANDARD_NAME.match(n)]
        custom = [n for n in all_names if not STANDARD_NAME.match(n)]
        manifest["custom_objects_removed"] = custom
        dropped += [f"object {n} (name is not a standard upper-case object name)" for n in custom]
        # remove the custom objects from the saved inspect data
        for parent in data.iter():
            for child in list(parent):
                n = child.attrib.get("typename") or child.attrib.get("Name") or child.attrib.get("name") or (child.text or "").strip()
                if n in custom:
                    parent.remove(child)
        guarded_write(os.path.join(args.out, "inspect.xml"), ET.tostring(data, encoding="unicode"))
        print(f"inspect: {len(all_names)} objects, {len(custom)} treated as custom and dropped")
        time.sleep(args.delay)
    manifest["objects"] = len(names)

    # 3. lookups, batched
    for i in range(0, len(names), args.batch):
        batch = names[i:i + args.batch]
        fx = "".join(f'      <function controlid="{escape(n)}"><lookup><object>{escape(n)}</object></lookup></function>\n' for n in batch)
        try:
            _, results = parse_response(post(session_endpoint, request_xml(sender_id, sender_pw, auth, fx, str(uuid.uuid4()))))
        except (urllib.error.URLError, RuntimeError, ET.ParseError) as e:
            for n in batch:
                manifest["lookups_failed"][n] = f"request failed: {e}"
            print(f"  !! batch {batch[0]}..{batch[-1]}: {e}", file=sys.stderr)
            time.sleep(args.delay)
            continue
        for r in results:
            n = r.findtext("controlid")
            if r.findtext("status") != "success":
                manifest["lookups_failed"][n] = result_error(r)
                continue
            data = r.find("data")
            if data is None:
                manifest["lookups_failed"][n] = "no data element"
                continue
            removed = scrub_fields(data)
            if removed:
                manifest["custom_fields_removed"][n] = removed
                dropped += [f"field {n}.{f} (ISCUSTOM true)" for f in removed]
            guarded_write(os.path.join(args.out, "lookup", f"{n}.xml"), ET.tostring(data, encoding="unicode"))
            manifest["lookups_ok"] += 1
        done = min(i + args.batch, len(names))
        if done % 50 < args.batch or done == len(names):
            print(f"  lookup {done}/{len(names)} ok={manifest['lookups_ok']} failed={len(manifest['lookups_failed'])}")
        time.sleep(args.delay)

    guarded_write(os.path.join(args.out, "dropped.txt"), "\n".join(dropped) + ("\n" if dropped else ""))
    guarded_write(os.path.join(args.out, "manifest.json"), json.dumps(manifest, indent=1, sort_keys=True))
    print(f"done: {manifest['lookups_ok']} object definitions in {os.path.join(args.out, 'lookup')}, "
          f"{len(manifest['lookups_failed'])} failed, {sum(len(v) for v in manifest['custom_fields_removed'].values())} custom fields "
          f"and {len(manifest['custom_objects_removed'])} custom objects dropped (see dropped.txt)")


if __name__ == "__main__":
    main()
