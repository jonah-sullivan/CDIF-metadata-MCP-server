# CDIF readiness of NCRIS facility metadata (2026-11-19)

How well a random sample of public dataset records from each facility's GeoNetwork catalogue converts to the [CDIF Discovery profile](https://w3id.org/cdif/discovery/1.1), and what is missing. Each record's ISO 19115 XML was converted with this repository's crosswalk (see [iso19115-cdif-crosswalk.md](iso19115-cdif-crosswalk.md)) and validated against the CDIF JSON Schema and SHACL rules.

## Method

- **Sample:** 50 records per catalogue, drawn at random from its public records with resource type `dataset`, using seed 358282 (each catalogue's seed is derived from it).
- **Per-record results:** [facility-report-2026-11-19.csv](facility-report-2026-11-19.csv).
- **Identifiers:** checked offline for every record (persistent identifiers, ORCIDs, RORs, check digits). They were also resolved (DOIs through DataCite and Crossref, ORCID, ROR, other URIs by GET), and vocabulary terms dereferenced and checked against their vocabularies.
- **Files that determined the results** (first 12 hex digits of their SHA-256; a different hash means results may differ):

| File | SHA-256 |
| --- | --- |
| CDIF JSON Schema | `9e130e0b8acf` |
| CDIF SHACL rules | `bfaa4d40720e` |
| CDIF JSON-LD frame | `bc99df451640` |
| CDIF framing script | `d6c31c94860e` |
| Crosswalk | `c866c03b8923` |
| GeoNetwork access | `8504fd078609` |
| Validator (MCP server) | `9b6e64e88189` |
| Identifier checks | `53b0757b9bfa` |
| Identifier resolution | `b738302803fb` |
| Vocabulary checks | `6b10d365b660` |
| Identifier report | `03f688ee041a` |

Percentages are of converted records unless stated. A record's result depends on both the source metadata and the crosswalk: a failure can come from a gap in the source, a limit of the crosswalk, or a problem in CDIF's own artefacts (listed in the crosswalk documentation).

**CDIF valid** counts records that pass after conversion, including where the crosswalk filled a required property from a fallback: the metadata record's identifier for `schema:identifier`, the metadata record's date for `schema:dateModified`, or a licence recognised from its name. **Valid from source values alone** counts only records that needed none of these fallbacks, and is the better measure of how ready the source metadata is.

## Summary

| | TERN | IMOS/AODN | AuScope |
| --- | --- | --- | --- |
| GeoNetwork | 4.4.12 | 3.12.7 | 4.4.12 |
| Dataset records in catalogue | 3,082 | 1,650 | 1,576 |
| Sampled | 50 | 50 | 50 |
| Converted (of sample) | 50 (100%) | 50 (100%) | 50 (100%) |
| **CDIF valid** | 50 (100%) | 50 (100%) | 48 (96%) |
| **Valid from source values alone** | 22 (44%) | 0 (0%) | 0 (0%) |
| Identifier from fallback | 28 (56%) | 47 (94%) | 45 (90%) |
| dateModified from fallback | 0 (0%) | 27 (54%) | 49 (98%) |
| JSON Schema valid | 50 (100%) | 50 (100%) | 48 (96%) |
| SHACL conforms (no violations) | 50 (100%) | 50 (100%) | 49 (98%) |
| DOI as identifier | 22 (44%) | 1 (2%) | 4 (8%) |
| Licence given as a URI | 49 (98%) | 48 (96%) | 49 (98%) |
| Licence recognised only from its name or badge | 0 (0%) | 2 (4%) | 0 (0%) |
| No licence | 0 (0%) | 0 (0%) | 1 (2%) |
| Names a creator | 50 (100%) | 19 (38%) | 1 (2%) |
| Variables with vocabulary URIs | 0 (0%) | 45 (90%) | 0 (0%) |
| Keywords with vocabulary URIs | 50 (100%) | 30 (60%) | 0 (0%) |
| Dataset has a persistent identifier | 22 (44%) | 1 (2%) | 5 (10%) |
| People with an ORCID | 0 of 75 (0%) | 0 of 84 (0%) | 0 of 36 (0%) |
| Organisations with a ROR | 0 of 142 (0%) | 0 of 85 (0%) | 0 of 38 (0%) |
| All checked identifiers resolve | 49 (98%) | 48 (96%) | 39 (78%) |
| Dataset DOI identifies a publication | 8 (16%) | 1 (2%) | 4 (8%) |
| Vocabulary terms with a machine-readable description | 1086 of 1176 (92%) | 239 of 290 (82%) | - |
| Term names matching their vocabulary label | 1086 of 1086 (100%) | 212 of 239 (89%) | - |
| Deprecated vocabulary terms | 0 | 1 | 0 |
| RVA-hosted vocabularies used | 2 | 6 | 0 |

## Why records fail validation

Most common JSON Schema problems and SHACL violations, by number of records.

### TERN

None.

### IMOS/AODN

None.

### AuScope

| Records | Message |
| --- | --- |
| 1 | $: 'schema:license' is a required property |
| 1 | $['schema:distribution'][0]: 'schema:contentUrl' is a required property |
| 1 | $['schema:funding'][0]: 'schema:funder' is a required property |
| 1 | A WebAPI must specify terms of service (schema:termsOfService) as a string, IRI, or CreativeWork. |
| 1 | To meet the requirements for FAIR data, information about licenses or other security, usage, or access limitations must be described |

## Missing required properties

CDIF required properties the source records cannot supply.

### TERN

None.

### IMOS/AODN

None.

### AuScope

| Records | Message |
| --- | --- |
| 1 | schema:license or schema:conditionsOfAccess |

## Inferences and fallbacks

Values the crosswalk had to infer or take from a fallback source, most common first.

### TERN

| Records | Message |
| --- | --- |
| 28 | @id is the schema:url value (no resolvable resource identifier). |
| 28 | The resource has no identifier; schema:identifier is the metadata record identifier. |
| 9 | DOI taken from an online resource link (e.g. protocol 'DOI'), not from the citation identifiers. |
| 3 | WebAPI termsOfService is the resource licence or access conditions (ISO has no per-service terms). |

### IMOS/AODN

| Records | Message |
| --- | --- |
| 49 | schema:url is the catalogue's metadata page (no landing page or DOI). |
| 48 | @id is the schema:url value (no resolvable resource identifier). |
| 47 | The resource has no identifier; schema:identifier is the metadata record identifier. |
| 27 | No resource revision date; schema:dateModified is the metadata record's date. |
| 25 | WebAPI termsOfService is the resource licence or access conditions (ISO has no per-service terms). |

### AuScope

| Records | Message |
| --- | --- |
| 49 | No resource revision date; schema:dateModified is the metadata record's date. |
| 48 | WebAPI termsOfService is the resource licence or access conditions (ISO has no per-service terms). |
| 45 | The resource has no identifier; schema:identifier is the metadata record identifier. |
| 39 | @id is the schema:url value (no resolvable resource identifier). |
| 39 | schema:url is the catalogue's metadata page (no landing page or DOI). |

## Identifier and vocabulary findings

Problems and warnings from the identifier checks, by number of records.

### TERN

| Records | Finding |
| --- | --- |
| 50 | Vocabulary terms with no machine-readable description |
| 41 | Vocabulary URI that does not resolve |
| 28 | Dataset has no persistent identifier |
| 8 | Dataset DOI that identifies a publication |
| 1 | Identifier that does not exist |

### IMOS/AODN

| Records | Finding |
| --- | --- |
| 49 | Dataset has no persistent identifier |
| 28 | Vocabulary named but not identified by a URI |
| 25 | Identifier that is not a URI |
| 24 | Keyword name that differs from its vocabulary label |
| 17 | Vocabulary URI that does not resolve |
| 15 | One URI used as the identifier of several different terms |
| 1 | Dataset DOI that identifies a publication |
| 1 | Deprecated vocabulary term |

### AuScope

| Records | Finding |
| --- | --- |
| 45 | Dataset has no persistent identifier |
| 44 | Identifier that is not a URI |
| 11 | Identifier that returns an error |
| 4 | Dataset DOI that identifies a publication |

## Vocabularies hosted in RVA

- **TERN:** Australian and New Zealand Standard Research Classification 2020: Fields of Research (version 2020, current); Australian and New Zealand Standard Research Classification 2020: Socio-Economic Objectives (version 2020, current)
- **IMOS/AODN:** AODN Discovery Parameter Vocabulary (version-1-7, current); AODN Geographic Extents Vocabulary (version-4-0, current); AODN Instrument Vocabulary (version-2-4, current); AODN Organisation Vocabulary (version-3-0, current); AODN Platform Vocabulary (version-7-0, current); AODN Units of Measure Vocabulary (version-1-2, current)
- **AuScope:** none

## Records not converted

None.
