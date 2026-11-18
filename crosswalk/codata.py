"""How the ISO 19115 crosswalk relates to CODATA's crosswalk spreadsheet.

CODATA's spreadsheet is doc-corediscovery/archive/
metadataCrosswalksCDIF-DCAT-ISOetc.xlsx, sheet "crosswalks"; row numbers
below are its spreadsheet row numbers (row 1 is the header). It is an
archived working document, not a normative part of the CDIF profile.

Used by scripts/gen_crosswalk_docs.py to write
docs/iso19115-cdif-crosswalk.md.
"""

from dataclasses import dataclass

SPREADSHEET = (
    "doc-corediscovery/archive/metadataCrosswalksCDIF-DCAT-ISOetc.xlsx"
)


@dataclass(frozen=True)
class Reference:
    rows: str  # spreadsheet rows the rule is based on ("" if none)
    differs: str = ""  # how and why this tool departs from them


# Keyed by MAPPINGS[*].cdif. A test checks every mapping has an entry.
REFERENCES: dict[str, Reference] = {
    "@id": Reference("2"),
    "schema:identifier": Reference("2"),
    "schema:sameAs": Reference("44"),
    "schema:name": Reference("3"),
    "schema:alternateName": Reference("45"),
    "schema:description": Reference("4"),
    "schema:version": Reference("8"),
    "schema:dateCreated": Reference("5"),
    "schema:datePublished": Reference("7"),
    "schema:dateModified": Reference(
        "6",
        "CODATA uses dateType lastUpdate only. GeoNetwork records use "
        "revision for the same purpose, so both are read. The fallbacks to "
        "metadata, publication and creation dates are this tool's.",
    ),
    "schema:inLanguage": Reference("52"),
    "schema:creator": Reference(
        "11",
        "CODATA lists editor among the creator roles (row 11) but also maps "
        "editor to a contributor Role (row 23); this tool follows row 23. "
        "Using principal investigators when there is no author or "
        "originator is this tool's addition.",
    ),
    "schema:publisher": Reference(
        "22",
        "CODATA maps publisher to a contributor Role. The CDIF schema has "
        "schema:publisher, which this tool uses.",
    ),
    "schema:contributor": Reference("12, 23, 25"),
    "schema:provider": Reference(
        "",
        "Not in CODATA's crosswalk. Distributor contacts map naturally to "
        "schema:provider, which the CDIF schema defines.",
    ),
    "schema:funding": Reference(
        "26",
        "CODATA also maps MD_Identification/credit. Credit is free-text "
        "acknowledgement, so it is left unmapped rather than turned into a "
        "MonetaryGrant.",
    ),
    "schema:keywords": Reference("13, 15"),
    "schema:spatialCoverage": Reference("16"),
    "schema:temporalCoverage": Reference("17"),
    "schema:license": Reference(
        "18",
        "CODATA reads reference/CI_Citation, or useLimitation text, in "
        "MD_LegalConstraints. NCRIS records also state licences in generic "
        "MD_Constraints and only as licence-badge images, so every "
        "constraint type is read and licences are also recognised from "
        "Creative Commons names and badges (reported as inferences).",
    ),
    "schema:conditionsOfAccess": Reference(
        "19, 20",
        "Also reads MD_Constraints, not only MD_LegalConstraints.",
    ),
    "schema:url": Reference(
        "34",
        "CODATA uses transfer options with function 'information'. NCRIS "
        "records give that function to many links that are not landing "
        "pages (home pages, THREDDS catalogues), so this tool uses the "
        "citation link that is labelled as a landing page or contains the "
        "record's identifier, then the DOI, then the catalogue page.",
    ),
    "schema:distribution": Reference(
        "33, 71-74",
        "CODATA classifies links by function = download. GeoNetwork "
        "records rely on the protocol (e.g. WWW:DOWNLOAD-1.0-http--download, "
        "OGC:WMS-1.3.0-http-get-map), so the protocol is used first. "
        "CODATA's WebAPI rows give no ISO source; this tool builds WebAPIs "
        "from OGC, ESRI, OPeNDAP and THREDDS protocols, and fills the "
        "required termsOfService from the resource licence.",
    ),
    "schema:relatedLink": Reference(
        "30, 55",
        "CODATA maps associatedResource, which is not implemented yet (see "
        "below). Additional documentation and non-download transfer links "
        "are this tool's additions.",
    ),
    "schema:variableMeasured": Reference(
        "35, 75, 76",
        "CODATA maps the ISO 19110 feature catalogue. This tool reads "
        "coverage descriptions (MD_SampleDimension, MD_Band), which is "
        "where IMOS records list their variables with NERC vocabulary "
        "URIs.",
    ),
    "prov:wasGeneratedBy": Reference(
        "69",
        "CODATA also maps the lineage statement to measurementTechnique of "
        "a variable (row 64). A record-level statement is not about one "
        "variable, so here it describes the generating Activity.",
    ),
    "schema:subjectOf": Reference(
        "38-42, 49",
        "CODATA maps metadata dates to subjectOf dateModified and "
        "dateCreated; the CDIF schema's subjectOf has sdDatePublished, "
        "which is used instead.",
    ),
}


@dataclass(frozen=True)
class NotImplemented:
    cdif: str
    iso: str
    rows: str
    why: str


# CODATA crosswalk rows with an ISO source that this tool does not map yet.
NOT_IMPLEMENTED: list[NotImplemented] = [
    NotImplemented(
        "schema:relatedLink",
        "MD_Identification/associatedResource",
        "30, 55-62",
        "associationType values need mapping to link relations.",
    ),
    NotImplemented(
        "prov:wasDerivedFrom",
        "resourceLineage/LI_Lineage/source",
        "58, 70",
        "Not yet implemented.",
    ),
    NotImplemented(
        "schema:variableMeasured",
        "contentInfo/MD_FeatureCatalogueDescription (ISO 19110)",
        "35",
        "The feature catalogue is usually a separate record that would "
        "have to be fetched.",
    ),
    NotImplemented(
        "schema:distribution/schema:encodingFormat",
        "distributionInfo//distributionFormat",
        "50",
        "Not yet implemented.",
    ),
    NotImplemented(
        "schema:distribution/schema:contentSize",
        "distributionInfo//transferOptions//transferSize",
        "46",
        "Not yet implemented.",
    ),
    NotImplemented(
        "schema:repeatFrequency",
        "distributionInfo//transferOptions//transferFrequency",
        "67",
        "Not yet implemented.",
    ),
    NotImplemented(
        "schema:conditionsOfAccess (embargo)",
        "citation date with dateType released",
        "43",
        "Not yet implemented.",
    ),
    NotImplemented(
        "dqv:hasQualityMeasurement",
        "dataQualityInfo/DQ_DataQuality",
        "47",
        "Needs a mapping of ISO 19157 quality reports.",
    ),
    NotImplemented(
        "schema:subjectOf/schema:inLanguage",
        "MD_Metadata language (defaultLocale in 19115-3)",
        "53",
        "Not a property of subjectOf in the CDIF schema.",
    ),
    NotImplemented(
        "schema:funding",
        "MD_Identification/credit",
        "26",
        "Free-text acknowledgement; see schema:funding above.",
    ),
]
