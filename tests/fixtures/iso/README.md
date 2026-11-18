# ISO 19115 crosswalk fixtures

Ten metadata records from three Australian NCRIS facility catalogues, used to
develop and test the ISO 19115 → CDIF Discovery crosswalk. `manifest.json`
lists each record's source URL, the date fetched, its SHA-256, and why it was
chosen. To refresh them:

```bash
.venv/bin/python scripts/fetch_iso_fixtures.py            # all
.venv/bin/python scripts/fetch_iso_fixtures.py --only aodn-sst-l3s.xml
```

The script fetches `{catalogue}/srv/api/records/{uuid}/formatters/xml`.
Refetching may change a file, because catalogues edit records. Golden-output
tests should then be reviewed, not blindly regenerated.

## Sources

| Facility | Catalogue | GeoNetwork | Records held | Dialects |
| --- | --- | --- | --- | --- |
| TERN | geonetwork.tern.org.au | 4.4 | ~3,080 | all ISO 19115-3 |
| IMOS / AODN | catalogue-imos.aodn.org.au | 3.12 | ~1,860 | ISO 19115-3 (sampled) |
| AuScope | portal.auscope.org.au | 4.4 | ~1,630 | 1,575 ISO 19115-3, 57 ISO 19139 (51 of them services) |

The counts are as of 2026-11-18. AODN's GeoNetwork 3.12 rejects the
`/api/search` endpoint (403). Use `/srv/eng/q` or CSW there instead.

## Licences

None of the records has `metadataConstraints`, so no record states a licence
for the metadata itself. The licences each record states for the described
resource are:

| File | Resource licence as stated |
| --- | --- |
| tern-* (3) | CC BY 4.0 (text in `MD_LegalConstraints`, with Anchors) |
| aodn-* (3) | CC BY 4.0 (licence graphic link and text in `MD_LegalConstraints`) |
| auscope-rosebery-3d-model.xml | CC BY 4.0 (URL in `MD_LegalConstraints`) |
| auscope-calperum-czo.xml | CC BY 4.0 (URL in generic `MD_Constraints`, not `MD_LegalConstraints`) |
| auscope-gsnsw-wms-service.xml | "CC-BY-4.0 International Attribution" (text) |
| auscope-gsv-mineral-occurrences.xml | none stated |

All of these are publicly harvestable catalogue records. Before publishing
this repo, confirm that redistributing them is acceptable. If it isn't, keep
only `manifest.json` and fetch the records at test time.

## What the fixtures show (input for the mapping)

- **Licence is encoded at least five ways**: a CC URL, a licence-graphic
  link, free text, an SPDX-like code, and a URL under generic
  `MD_Constraints`. So the crosswalk must check all `*_Constraints`, not
  just `MD_LegalConstraints`. One record has no licence, which should be
  reported as a missing required CDIF property (license or
  conditionsOfAccess).
- **Spatial extent is not always a bounding box.**
  `aodn-ran-ctd-profiles.xml` has 12 `EX_BoundingPolygon` elements and no
  `EX_GeographicBoundingBox`. Three records have 2–3 boxes.
- **`gcx:Anchor` is common at TERN** (26–82 per record) and appears at AODN.
  These carry vocabulary and PID URIs, which should become `DefinedTerm`
  `@id`s and identifier URLs rather than plain strings.
- **`gco:nilReason` is common** (up to 141 in one record). These empty
  elements must be skipped, not emitted as empty strings.
- **No `PT_FreeText`** (multilingual text) in any fixture, so it is low
  priority.
- **`auscope-rosebery-3d-model.xml` has no `MD_ScopeCode`.** Treat a
  missing scope as dataset, with a note in the crosswalk report.
- **`auscope-gsnsw-wms-service.xml` has scope `service`.** It is the
  out-of-scope test case.
