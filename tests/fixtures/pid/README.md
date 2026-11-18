# Identifier and vocabulary service fixtures

Recorded responses from the services that `pidcheck` will use, so its tests
run offline. `manifest.json` lists each exchange: the request, every
redirect hop, the final URL, status and content type, and the saved body.
Large or personal JSON is trimmed to the fields the checks use (an ORCID
record keeps only the name), and HTML pages are cut to 2,000 bytes. To
re-record:

```bash
.venv/bin/python scripts/record_pid_fixtures.py            # all
.venv/bin/python scripts/record_pid_fixtures.py --only ror-inactive
```

The identifiers come from the ISO fixtures (`../iso/`) after conversion.

## Services

As found on 2026-11-19.

| Kind | Service | How to check | Notes |
| --- | --- | --- | --- |
| DOI exists | `doi.org/api/handles/{doi}` | `responseCode` 1 = found, 100 = not found (HTTP 404) | No landing page is fetched |
| DOI agency | `doi.org/ra/{doi},{doi}` | `RA` per DOI | Tells DataCite (data) from Crossref (publications) |
| DOI type | `api.datacite.org/dois/{doi}`, `api.crossref.org/works/{doi}` | `resourceTypeGeneral`, `state`; Crossref `type` | |
| ORCID | `pub.orcid.org/v3.0/{id}/person` | 200 = exists, 404 = not | Anonymous works (25k reads/day/IP). Optional `/read-public` token: client credentials, then `Authorization: Bearer` |
| ROR | `api.ror.org/v2/organizations/{id}` | `status`, `relationships` (successor) | Anonymous up to 2,000 req / 5 min / IP. Optional `Client-Id` header (registration paused) |
| NERC NVS | concept URI, conneg | Turtle, RDF/XML, JSON-LD | Canonical URI ends in `/`; `owl:deprecated`; collections, so often no `skos:inScheme` |
| GCMD | concept URI | always RDF/XML labelled `application/xml` | Redirects to `cmr.earthdata.nasa.gov`; `xml:base` keeps the GCMD URI |
| ANZSRC 2020 | `linked.data.gov.au` URI, conneg | Turtle, RDF/XML (JSON-LD 406) | Served by RVA's linked-data API |
| TERN CV | `linked.data.gov.au` URI, conneg | Turtle as `application/x-turtle` | 3 redirects to `linkeddata.tern.org.au` |
| AODN vocabularies | `vocab.aodn.org.au` URI | redirect lands on RVA `resource.html`: HTML whatever `Accept` says; add `&_format=ttl` | Hosted in RVA; the redirect names the vocabulary version |
| QUDT | unit URI | Turtle only | `qudt:Unit` with `rdfs:label`, not a SKOS concept |
| AFD | taxon URI | 504 every time | Treat as unreachable, not invalid |
| TERN parties | `w3id.org/tern/resources/...` | resolves to an HTML viewer | Persistent, but no machine-readable form |
| RVA registry | `vocabs.ardc.edu.au/registry/api/resource/vocabularies[/{id}]` | JSON with versions and access points | No lookup by URI. Listing returned 500 of up to 774 ids (may be capped) |

General behaviour to handle:

- **HEAD is unreliable.** NCI's server answers HEAD with 400 and GET with
  200, so resolution falls back to GET.
- **Content types vary.** `application/x-turtle` and generic `application/xml`
  carry RDF; parse by trying the likely format, not only by MIME type.
- **Concept URIs** may be found as given, with or without a trailing slash,
  or under https rather than http.
- **Labels** are `skos:prefLabel`, or `rdfs:label` (QUDT).

## RVA registration

RVA has no lookup by URI, so registration can be shown two ways:

1. **Hosted in RVA (definitive).** The term resolves into
   `vocabs.ardc.edu.au/repository/api/lda/{owner}/{vocabulary}/{version}`,
   which names the vocabulary and version. The registry then says whether
   that version is current or superseded.
2. **Registered, served elsewhere (heuristic).** E.g. TERN Controlled
   Vocabularies (RVA id 641) has a web-page access point on
   `linkeddata.tern.org.au`, where its terms resolve. This can only be
   matched by host.

Anything else is reported as "not found in RVA", never "not registered".

## Findings in the bundled records

- **A journal article as the dataset identifier.** The AuScope Rosebery
  record's `@id` is `10.3390/min11111195`, a Crossref `journal-article`. The
  crosswalk took it from an online resource with protocol `DOI`. The TERN
  and Calperum DOIs are DataCite `Dataset`s, state `findable`.
- **An inactive organisation.** ROR `00892tw58` (The University of Adelaide)
  is `inactive`; its successor is Adelaide University (`028g18b61`).
- **A dead code-space link.** AODN variables use
  `http://cf-pcmdi.llnl.gov/documents/cf-standard-names` as the code space for
  CF standard names; it returns 404.
- **Directory URLs as party identifiers.** NCI records identify people
  with `https://geonetwork.nci.org.au/geonetwork/srv/resources/persons/`,
  the same URL for everyone.
- **A term set identified by a web page.** ANZSRC terms use
  `linked.data.gov.au` URIs, but the record's term set is an ABS web page.
- **Labels match.** Every keyword with a vocabulary URI that was checked has
  a name identical to the vocabulary's label (these come from `gcx:Anchor`).
  Hierarchical GCMD strings in AODN records have no URI at all.
