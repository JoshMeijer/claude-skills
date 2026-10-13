"""Tests for the object-definition dump helpers and the snapshot builder (maintenance only; not packaged).

    python -m unittest skills/sageintacct-assistant/scripts/test_build_object_snapshot.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dump_object_definitions as dump  # noqa: E402

LOOKUP = ('<data listtype="All" count="1"><Type Name="{name}" DocumentType=""><Fields>'
          '<Field><ID>RECORDNO</ID><LABEL>Record number</LABEL><DESCRIPTION>Record Number</DESCRIPTION><REQUIRED>false</REQUIRED>'
          '<READONLY>true</READONLY><DATATYPE>INTEGER</DATATYPE><ISCUSTOM>false</ISCUSTOM></Field>'
          '<Field><ID>STATE</ID><LABEL>State</LABEL><DESCRIPTION></DESCRIPTION><REQUIRED>true</REQUIRED><READONLY>false</READONLY>'
          '<DATATYPE>TEXT</DATATYPE><VALIDVALUES><VALIDVALUE>Draft</VALIDVALUE><VALIDVALUE>Posted</VALIDVALUE></VALIDVALUES>'
          '<ISCUSTOM>false</ISCUSTOM></Field>{extra}</Fields><Relationships><Relationship><OBJECTPATH>JOURNAL</OBJECTPATH>'
          '<OBJECTNAME>JOURNAL</OBJECTNAME><LABEL></LABEL><RELATIONSHIPTYPE>MANY2ONE</RELATIONSHIPTYPE><RELATEDBY>JOURNAL</RELATEDBY>'
          '</Relationship></Relationships></Type></data>')
CUSTOM = ('<Field><ID>X_CUSTOM</ID><LABEL>Custom</LABEL><DESCRIPTION></DESCRIPTION><REQUIRED>false</REQUIRED><READONLY>false</READONLY>'
          '<DATATYPE>TEXT</DATATYPE><ISCUSTOM>true</ISCUSTOM></Field>')
INSPECT = ('<data listtype="All" count="4"><type typename="GLBATCH">Journal Entry</type><type typename="SODOCUMENT">Sales document</type>'
           '<type typename="SODOCUMENT">Sales Order</type><type typename="SODOCUMENT">SYS-Fulfillment Shipper</type></data>')


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def make_dump(folder, custom=False):
    os.makedirs(os.path.join(folder, "lookup"))
    with open(os.path.join(folder, "inspect.xml"), "w", encoding="utf-8") as f:
        f.write(INSPECT)
    for name in ("GLBATCH", "SODOCUMENT"):
        with open(os.path.join(folder, "lookup", name + ".xml"), "w", encoding="utf-8") as f:
            f.write(LOOKUP.format(name=name, extra=CUSTOM if (custom and name == "GLBATCH") else ""))
    with open(os.path.join(folder, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"release": "test", "dumped": "2026-10-13T00:00:00Z", "lookups_failed": {},
                   "custom_objects_removed": ["my_obj"], "custom_fields_removed": {"GLENTRY": ["GLE_FEE", "GLE_FROM"]}}, f)


class DumpHelpers(unittest.TestCase):
    def test_scrub_fields_removes_only_custom(self):
        d = ET.fromstring(LOOKUP.format(name="GLBATCH", extra=CUSTOM))
        removed = dump.scrub_fields(d)
        self.assertEqual(removed, ["X_CUSTOM"])
        self.assertEqual([f.findtext("ID") for f in d.iter("Field")], ["RECORDNO", "STATE"])

    def test_inspect_names_and_collapse(self):
        names = dump.inspect_names(ET.fromstring(INSPECT))
        self.assertEqual(names, ["GLBATCH", "SODOCUMENT"])
        xml, dropped = dump.collapse_inspect(INSPECT)
        self.assertEqual(dropped, {"SODOCUMENT": 2})
        kept = ET.fromstring(xml)
        self.assertEqual([t.get("typename") for t in kept.iter("type")], ["GLBATCH", "SODOCUMENT"])
        self.assertEqual([t.text for t in kept.iter("type")][1], "Sales document")  # the generic label survives
        self.assertNotIn("SYS-", xml)

    def test_pack_writes_counts_only_and_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_dump(os.path.join(tmp, "dump"))
            z = os.path.join(tmp, "out", "d.zip")
            dump.pack(os.path.join(tmp, "dump"), z)
            with zipfile.ZipFile(z) as zf:
                m = json.loads(zf.read("manifest.json"))
                self.assertEqual(m["custom_fields_removed"], {"GLENTRY": 2})
                self.assertEqual(m["custom_objects_removed"], 1)
                self.assertEqual(m["inspect_entries_collapsed"], {"SODOCUMENT": 2})
                self.assertEqual(sorted(zf.namelist()), ["inspect.xml", "lookup/GLBATCH.xml", "lookup/SODOCUMENT.xml", "manifest.json"])
            # a leaked envelope element refuses the whole pack and leaves no zip behind
            with open(os.path.join(tmp, "dump", "lookup", "GLBATCH.xml"), "a", encoding="utf-8") as f:
                f.write("<control><senderid>x</senderid></control>")
            z2 = os.path.join(tmp, "out", "d2.zip")
            with self.assertRaises(SystemExit):
                dump.pack(os.path.join(tmp, "dump"), z2)
            self.assertFalse(os.path.exists(z2))


class SnapshotBuilder(unittest.TestCase):
    def run_builder(self, dump_path, out):
        return subprocess.run([sys.executable, "-I", os.path.join(HERE, "build_object_snapshot.py"), "--dump", dump_path,
                               "--out", out, "--release", "2026 Release 3", "--verified", "2026-10-13"],
                              capture_output=True, text=True)

    def test_build_from_zip_and_line_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_dump(os.path.join(tmp, "dump"))
            z = os.path.join(tmp, "d.zip")
            dump.pack(os.path.join(tmp, "dump"), z)
            out = os.path.join(tmp, "snap")
            r = self.run_builder(z, out)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(sorted(os.listdir(out)), ["INDEX.md", "fields-01.md", "objects.md"])
            objects = read(os.path.join(out, "objects.md"))
            bundle = read(os.path.join(out, "fields-01.md")).split("\n")
            rows = re.findall(r"^\| `([A-Z]+)` \| ([^|]*) \| (\d+) \| (\d+) \| \[(fields-01\.md)\]\(fields-01\.md#[a-z]+\) \| (\d+) \| (\d+) \|$", objects, re.M)
            self.assertEqual([(r[0], r[1].strip(), int(r[2]), int(r[3])) for r in rows],
                             [("GLBATCH", "Journal Entry", 2, 1), ("SODOCUMENT", "Sales document", 2, 1)])
            for name, _, _, _, _, line, n in rows:
                self.assertTrue(bundle[int(line) - 1].startswith(f'<a id="{name.lower()}"'))
                self.assertTrue(bundle[int(line)].startswith(f"## {name}"))
            self.assertIn("`Draft`, `Posted`", "\n".join(bundle))
            self.assertIn("1 custom objects and 2 custom fields were removed", read(os.path.join(out, "INDEX.md")))

    def test_refuses_surviving_custom_field_and_keeps_old_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_dump(os.path.join(tmp, "dump"), custom=True)
            out = os.path.join(tmp, "snap")
            os.makedirs(out)
            with open(os.path.join(out, "objects.md"), "w", encoding="utf-8") as f:
                f.write("previous\n")
            r = self.run_builder(os.path.join(tmp, "dump"), out)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("custom fields survive", r.stderr + r.stdout)
            self.assertEqual(read(os.path.join(out, "objects.md")), "previous\n")


if __name__ == "__main__":
    unittest.main()
