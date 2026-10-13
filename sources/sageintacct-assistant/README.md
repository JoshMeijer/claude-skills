# sageintacct-assistant: builder inputs

Machine-readable inputs that `skills/sageintacct-assistant/scripts/` needs to regenerate a bundled reference. They
are kept here, outside the skill folder, so they are never packaged or loaded at runtime, and so that anyone can
rebuild the generated files and review a builder change by diffing its output.

| File | What it is | Licence and scrubbing | Regenerates |
|---|---|---|---|
| `api.v3.0.xsd` | `v3.0/api.xsd` from Sage's public schema repository https://github.com/Intacct/intacct_dtd, the XML Schema the Web Services gateway validates DTD 3.0 requests against; commit `89275604db` of 2024-05-07 (the repository's README states the schemas are no longer enhanced since the REST API went GA in 2025 Release 1). SHA-256 `f6265062d4fa0554bfbf2e2a789a31fe7bcf018a8cd8e3f69f18cf051561fa49`. | Apache License 2.0 (the repository's `LICENSE`); redistributed unchanged. Nothing to scrub: it holds element definitions only. | `skills/sageintacct-assistant/references/xml-api/functions.md` |
| `object-definitions-<release>/` | **Pending.** The scrubbed output of `scripts/dump_object_definitions.py` run against a clean developer company: `inspect.xml`, `lookup/<OBJECT>.xml`, `manifest.json`. | Custom (`ISCUSTOM`) fields and non-standard objects removed by the script; the `<control>` block (sender ID), session ID, endpoint, company ID and user ID are never written; reviewed by hand before committing (`MAINTENANCE.md`, "Object-definition snapshot"). | `skills/sageintacct-assistant/references/xml-api/snapshot/` |

The developer portal cache (developer.intacct.com, about 275 HTML pages) is **not** kept here: it is re-fetched by
`scripts/fetch_developer_portal.py` into a scratch folder, and the generated files cite the page URLs.

To regenerate the function grammar, run from the repository root:

```
python -I skills/sageintacct-assistant/scripts/build_functions_reference.py --xsd sources/sageintacct-assistant/api.v3.0.xsd --out skills/sageintacct-assistant/references/xml-api/functions.md --verified YYYY-MM-DD --xsd-note "commit 89275604db of 2024-05-07"
```

The full procedure is "Refreshing `references/xml-api/`" in `skills/sageintacct-assistant/MAINTENANCE.md`.
