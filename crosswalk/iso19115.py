"""ISO 19115 -> CDIF Discovery (v1.1) JSON-LD crosswalk.

Reads ISO 19115-3 (mdb: namespace, ISO 19115-1:2014) and ISO 19139 (gmd:
namespace, ISO 19115:2003) records, as served by GeoNetwork, and builds a
CDIF Discovery record plus a report of what was mapped, what was dropped
and which CDIF required properties have no ISO source.

The mapping follows CODATA's crosswalk spreadsheet
(doc-corediscovery/archive/metadataCrosswalksCDIF-DCAT-ISOetc.xlsx, columns
"ISO-19115:2003" and "ISO19115-1"). MAPPINGS below lists each rule, with the
spreadsheet's "Generic element" name, so the documentation can be generated
from the code.

The two dialects share most element local names (title, CI_Date,
EX_GeographicBoundingBox, CI_OnlineResource, ...), so paths are matched by
local name and only parties and metadata-level fields need dialect-specific
code.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from functools import cache
from typing import ClassVar

from lxml import etree

DIALECTS = {
    "http://standards.iso.org/iso/19115/-3/mdb/2.0": "iso19115-3",
    "http://www.isotc211.org/2005/gmd": "iso19139",
}
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
CDIF_PROFILES = [
    "https://w3id.org/cdif/core/1.1",
    "https://w3id.org/cdif/discovery/1.1",
]
CONTEXT = {
    "schema": "http://schema.org/",
    "dcterms": "http://purl.org/dc/terms/",
    "dcat": "http://www.w3.org/ns/dcat#",
    "prov": "http://www.w3.org/ns/prov#",
    "geosparql": "http://www.opengis.net/ont/geosparql#",
    "sf": "http://www.opengis.net/ont/sf#",
    "time": "http://www.w3.org/2006/time#",
}
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
ISO_CODELISTS = (
    "https://standards.iso.org/iso/19115/resources/Codelists/cat/codelists.xml"
)

# Resource scopes (MD_ScopeCode) that describe data, and so can be a
# schema:Dataset. Anything else (service, software, document, sensor...) is
# outside the CDIF Discovery profile's scope.
DATASET_SCOPES = {
    "dataset",
    "series",
    "nonGeographicDataset",
    "tile",
    "collectionSession",
    "fieldSession",
    "model",
    "productionSeries",
    "dimensionGroup",
}

# CI_RoleCode -> CDIF property. Roles not listed become schema:contributor
# with a schema:Role carrying the ISO role code.
CREATOR_ROLES = {"author", "coAuthor", "originator"}
PUBLISHER_ROLES = {"publisher"}
FUNDER_ROLES = {"funder", "sponsor"}

# Catalogue XML is untrusted: no entity expansion (XXE, billion laughs), no
# network access (DTDs, schemaLocation), no huge trees.
PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    huge_tree=False,
    load_dtd=False,
    remove_comments=True,
    remove_pis=True,
)


class CrosswalkError(ValueError):
    """The input is not an ISO 19115 record this crosswalk can read."""


@dataclass(frozen=True)
class Mapping:
    cdif: str  # CDIF property produced
    element: str  # "Generic element" row in CODATA's crosswalk sheet
    iso19115_3: str  # source path, ISO 19115-3
    iso19139: str  # source path, ISO 19139
    rule: str  # how values are transformed


_ID = "identificationInfo/*/citation/CI_Citation"
MAPPINGS: list[Mapping] = [
    Mapping(
        "@id",
        "Identifier [resource]",
        f"{_ID}/identifier/MD_Identifier/code",
        f"{_ID}/identifier/*/code",
        "The DOI (as https://doi.org/...) or, failing that, the first "
        "http(s) identifier; else the landing page; else urn:uuid of the "
        "metadata identifier when it is a UUID; else '#resource'.",
    ),
    Mapping(
        "schema:identifier",
        "Identifier [resource]",
        f"{_ID}/identifier/MD_Identifier",
        f"{_ID}/identifier/*",
        "Primary identifier as schema:PropertyValue (propertyID from "
        "codeSpace; DOIs get propertyID 'https://doi.org'). If the "
        "resource has no identifier, the metadata identifier is used "
        "and the report says so.",
    ),
    Mapping(
        "schema:sameAs",
        "Alternate identifier",
        f"{_ID}/identifier/MD_Identifier",
        f"{_ID}/identifier/*",
        "All other resource identifiers.",
    ),
    Mapping("schema:name", "Title", f"{_ID}/title", f"{_ID}/title", "Text."),
    Mapping(
        "schema:alternateName",
        "Alternate name",
        f"{_ID}/alternateTitle",
        f"{_ID}/alternateTitle",
        "Text.",
    ),
    Mapping(
        "schema:description",
        "Description",
        "identificationInfo/*/abstract",
        "identificationInfo/*/abstract",
        "Text; embedded HTML markup is removed.",
    ),
    Mapping(
        "schema:version",
        "version",
        f"{_ID}/edition",
        f"{_ID}/edition",
        "Text.",
    ),
    Mapping(
        "schema:dateCreated",
        "Date [resource created]",
        f"{_ID}/date/CI_Date[dateType=creation]",
        f"{_ID}/date/CI_Date[dateType=creation]",
        "ISO 8601 as given.",
    ),
    Mapping(
        "schema:datePublished",
        "Date [publication]",
        f"{_ID}/date/CI_Date[dateType=publication]",
        f"{_ID}/date/CI_Date[dateType=publication]",
        "ISO 8601 as given.",
    ),
    Mapping(
        "schema:dateModified",
        "Date [resource modified]",
        f"{_ID}/date/CI_Date[dateType=revision|lastUpdate]",
        f"{_ID}/date/CI_Date[dateType=revision|lastUpdate]",
        "Latest revision date. If there is none, falls back to the "
        "metadata record's revision date, then publication, then "
        "creation date; the report says which was used.",
    ),
    Mapping(
        "schema:inLanguage",
        "inLanguage [resource]",
        "identificationInfo/*/defaultLocale/PT_Locale/language",
        "identificationInfo/*/language",
        "Language code.",
    ),
    Mapping(
        "schema:creator",
        "Responsibility [creator]",
        f"{_ID}/citedResponsibleParty[role=author|coAuthor|originator]",
        f"{_ID}/citedResponsibleParty[role=author|coAuthor|originator]",
        "Ordered @list of Person/Organization. Individuals within an "
        "organisation become Persons affiliated with it. If there is no "
        "author or originator, principalInvestigator is used.",
    ),
    Mapping(
        "schema:publisher",
        "Responsibility [publisher]",
        f"{_ID}/citedResponsibleParty[role=publisher]",
        f"{_ID}/citedResponsibleParty[role=publisher]",
        "First publisher.",
    ),
    Mapping(
        "schema:contributor",
        "Responsibility [contributor]",
        f"{_ID}/citedResponsibleParty, identificationInfo/*/pointOfContact",
        f"{_ID}/citedResponsibleParty, identificationInfo/*/pointOfContact",
        "Every other role, as schema:Role with roleName a DefinedTerm "
        "from the ISO CI_RoleCode code list. Duplicates are removed.",
    ),
    Mapping(
        "schema:provider",
        "Distribution",
        "distributionInfo//distributorContact",
        "distributionInfo//distributorContact",
        "Distributors.",
    ),
    Mapping(
        "schema:funding",
        "Funding",
        f"{_ID}/citedResponsibleParty[role=funder|sponsor]",
        f"{_ID}/citedResponsibleParty[role=funder|sponsor]",
        "schema:MonetaryGrant with the party as schema:funder.",
    ),
    Mapping(
        "schema:keywords",
        "keyword [controlled vocabulary]",
        "identificationInfo/*/descriptiveKeywords/MD_Keywords, "
        "identificationInfo/*/topicCategory",
        "identificationInfo/*/descriptiveKeywords/MD_Keywords, "
        "identificationInfo/*/topicCategory",
        "DefinedTerm when the keyword has an Anchor URI or a thesaurus "
        "(inDefinedTermSet = thesaurus URL, else its title); otherwise a "
        "string. Topic categories are DefinedTerms in the ISO "
        "MD_TopicCategoryCode list.",
    ),
    Mapping(
        "schema:spatialCoverage",
        "Geographic Extent",
        "identificationInfo/*/extent/EX_Extent/geographicElement",
        "identificationInfo/*/extent/EX_Extent/geographicElement",
        "Bounding box -> Place with GeoShape box 'S W N E'. Bounding "
        "polygons -> Place with a WKT (Multi)Polygon in CRS84 via "
        "geosparql:hasGeometry, plus their envelope as a box. "
        "Geographic description codes -> Place name.",
    ),
    Mapping(
        "schema:temporalCoverage",
        "Temporal coverage",
        "identificationInfo/*/extent/EX_Extent/temporalElement",
        "identificationInfo/*/extent/EX_Extent/temporalElement",
        "ISO 8601 interval 'start/end'; an open or 'now' end is '..'.",
    ),
    Mapping(
        "schema:license",
        "Rights",
        "identificationInfo/*/resourceConstraints/*",
        "identificationInfo/*/resourceConstraints/*",
        "Licence URIs from any constraint type: reference citation "
        "links, Anchors, URLs in text, Creative Commons licence-button "
        "images, and Creative Commons names in text (e.g. 'CC BY 4.0'). "
        "Creative Commons URIs are normalised. Inferred licences are "
        "noted in the report.",
    ),
    Mapping(
        "schema:conditionsOfAccess",
        "Rights",
        "identificationInfo/*/resourceConstraints/*/"
        "(useLimitation|otherConstraints)",
        "identificationInfo/*/resourceConstraints/*/"
        "(useLimitation|otherConstraints)",
        "Text statements, deduplicated.",
    ),
    Mapping(
        "schema:url",
        "Distribution",
        f"{_ID}/onlineResource, metadataLinkage",
        "dataSetURI",
        "Landing page: the citation's online resource; else the DOI; "
        "else the metadata linkage (the catalogue page).",
    ),
    Mapping(
        "schema:distribution",
        "Distribution",
        "distributionInfo//transferOptions//onLine/CI_OnlineResource",
        "distributionInfo//transferOptions//onLine/CI_OnlineResource",
        "Download protocols/functions -> DataDownload (contentUrl). OGC, "
        "ESRI, OPeNDAP and THREDDS protocols -> WebAPI with serviceType "
        "and a GET EntryPoint. Other links go to schema:relatedLink.",
    ),
    Mapping(
        "schema:relatedLink",
        "Links",
        "identificationInfo/*/additionalDocumentation, distribution links",
        "distribution links",
        "LinkRole: 'describedby' for documentation, 'related' for other "
        "links.",
    ),
    Mapping(
        "schema:variableMeasured",
        "Variable measured",
        "contentInfo/MD_CoverageDescription//attribute/*",
        "contentInfo/MD_CoverageDescription/dimension/*",
        "PropertyValue: name, propertyID (Anchor URI, e.g. NERC P01), "
        "unitText and unitCode.",
    ),
    Mapping(
        "prov:wasGeneratedBy",
        "Provenance",
        "resourceLineage/LI_Lineage",
        "dataQualityInfo/DQ_DataQuality/lineage/LI_Lineage",
        "prov:Activity with the lineage statement as description; "
        "lineage documentation becomes prov:used CreativeWorks.",
    ),
    Mapping(
        "schema:subjectOf",
        "Identifier [metadata]",
        "metadataIdentifier, metadataLinkage, dateInfo, contact",
        "fileIdentifier, dateStamp, contact",
        "CatalogRecord node: @id from the metadata linkage or source "
        "URL, sdDatePublished from the metadata date, maintainer from "
        "the metadata contact, conformsTo CDIF core and discovery.",
    ),
]


# --------------------------------------------------------------------------
# Reading XML
# --------------------------------------------------------------------------


@cache
def _xp(path: str) -> etree.XPath:
    """'a/b//c' -> namespace-agnostic XPath matching on local names."""
    out = []
    for seg in path.split("/"):
        if seg in ("", ".", "..", "*"):
            out.append(seg)
        else:
            out.append(f"*[local-name()='{seg}']")
    return etree.XPath("/".join(out))


def _ln(el) -> str:
    return etree.QName(el).localname


VALUE_TAGS = {
    "CharacterString",
    "Anchor",
    "Date",
    "DateTime",
    "Decimal",
    "Real",
    "Integer",
    "Boolean",
    "URL",
    "LocalName",
    "FileName",
    "MimeFileType",
    "Measure",
    "Distance",
}


def _value(node) -> str | None:
    """Text of an ISO property element (or of its gco:/gcx: value child)."""
    if node is None:
        return None
    for child in node:
        if isinstance(child.tag, str) and _ln(child) in VALUE_TAGS:
            node = child
            break
    text = (node.text or "").strip()
    return text or None


def _href(node) -> str | None:
    if node is None:
        return None
    for el in [node, *node]:
        if isinstance(el.tag, str) and el.get(XLINK_HREF):
            return el.get(XLINK_HREF).strip() or None
    return None


def _codevalue(node) -> str | None:
    if node is None:
        return None
    for el in [node, *node]:
        if isinstance(el.tag, str) and el.get("codeListValue") is not None:
            return (
                el.get("codeListValue").strip()
                or (el.text or "").strip()
                or None
            )
    return _value(node)


class _Reader:
    """XPath helpers that remember which elements were read, so the report
    can list the parts of the record the crosswalk ignored."""

    def __init__(self):
        self.used: list = []

    def all(self, el, path: str) -> list:
        found = _xp(path)(el)
        self.used.extend(found)
        return found

    def one(self, el, path: str):
        found = self.all(el, path)
        return found[0] if found else None

    def text(self, el, path: str | None = None) -> str | None:
        return _value(el if path is None else self.one(el, path))

    def texts(self, el, path: str) -> list[str]:
        return [v for n in self.all(el, path) if (v := _value(n))]

    def href(self, el, path: str | None = None) -> str | None:
        return _href(el if path is None else self.one(el, path))

    def code(self, el, path: str | None = None) -> str | None:
        return _codevalue(el if path is None else self.one(el, path))


def parse(xml: bytes | str):
    """Parse untrusted XML and return the MD_Metadata element."""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    try:
        root = etree.fromstring(xml, PARSER)
    except etree.XMLSyntaxError as e:
        raise CrosswalkError(f"Not well-formed XML: {e}") from None
    if _ln(root) != "MD_Metadata":
        # e.g. a CSW GetRecordByIdResponse wrapping the record
        inner = root.xpath("//*[local-name()='MD_Metadata']")
        if not inner:
            raise CrosswalkError(
                f"No ISO MD_Metadata element found (root is {root.tag})."
            )
        root = inner[0]
    return root


def detect_dialect(root) -> str:
    ns = etree.QName(root).namespace
    if ns not in DIALECTS:
        raise CrosswalkError(
            f"Unsupported metadata namespace {ns!r}: expected ISO 19115-3 "
            f"(mdb 2.0) or ISO 19139 (gmd)."
        )
    return DIALECTS[ns]


# --------------------------------------------------------------------------
# Value helpers
# --------------------------------------------------------------------------

_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def _plain(s: str | None) -> str | None:
    """Strip HTML markup that some catalogues embed in abstracts."""
    if not s:
        return s
    if _TAG.search(s):
        s = re.sub(r"(?i)</p>|</div>|</h\d>", "\n\n", s)
        s = re.sub(r"(?i)<br\s*/?>|</li>", "\n", s)
        s = _TAG.sub("", s)
        s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*", "\n\n", s)
    return s.strip()


_DOI = re.compile(
    r"(?:doi:|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/\S+)", re.IGNORECASE
)


def _doi_url(*candidates: str | None) -> str | None:
    for c in candidates:
        if not c:
            continue
        m = _DOI.fullmatch(c.strip())
        if m and ("doi" in c.lower() or c.strip().startswith("10.")):
            return f"https://doi.org/{m.group(1)}"
    return None


def _is_url(s: str | None) -> bool:
    return bool(s) and re.match(r"https?://\S+$", s.strip()) is not None


def _iso_date(s: str | None) -> str | None:
    """Normalise catalogue date/times to the ISO 8601 form CDIF's SHACL
    pattern accepts: 'T' separator, no fractional seconds, '+hh:mm'."""
    if not s:
        return s
    s = s.strip()
    s = re.sub(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", r"\1T\2", s)
    s = re.sub(r"(T\d{2}:\d{2}:\d{2})\.\d+", r"\1", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    return s


# Identifier schemes recognised from codeSpace, authority or URL, mapped to
# the URI used as schema:propertyID.
KNOWN_SCHEMES = {
    "doi": "https://doi.org",
    "orcid": "https://orcid.org",
    "ror": "https://ror.org",
    "isni": "https://isni.org",
    "raid": "https://raid.org",
    "igsn": "https://igsn.org",
}


def _scheme(
    space: str | None, authority: str | None, url: str | None
) -> str | None:
    """schema:propertyID for an identifier. CDIF's SHACL wants an IRI or a
    string of at least 5 characters, so short labels like 'ROR' are mapped
    to their scheme URI and URL identifiers fall back to their host."""
    for label in (space, authority):
        if label and label.strip().lower().rstrip("/") in KNOWN_SCHEMES:
            return KNOWN_SCHEMES[label.strip().lower().rstrip("/")]
    for key, uri in KNOWN_SCHEMES.items():
        if url and re.match(rf"https?://(?:[\w-]+\.)?{key}\.org/", url):
            return uri
    for label in (space, authority):
        if label and len(label.strip()) >= 5:
            return label.strip()
    if url:
        m = re.match(r"(https?://[^/]+)", url)
        return m.group(1) if m else None
    return space or authority


def _num(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _dedupe(items: list) -> list:
    seen, out = set(), []
    for it in items:
        key = json.dumps(it, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def _term(name, code=None, term_set=None, identifier=None) -> dict:
    t = {"@type": ["schema:DefinedTerm"]}
    if name:
        t["schema:name"] = name
    if code:
        t["schema:termCode"] = code
    if identifier:
        t["schema:identifier"] = identifier
    if term_set:
        t["schema:inDefinedTermSet"] = term_set
    return t


def _property_value(
    value: str, property_id: str | None = None, url: str | None = None
) -> dict:
    pv = {"@type": ["schema:PropertyValue"], "schema:value": value}
    if property_id:
        pv["schema:propertyID"] = property_id
    if url:
        pv["schema:url"] = url
    return pv


# --------------------------------------------------------------------------
# The crosswalk
# --------------------------------------------------------------------------


class _Builder:
    def __init__(self, root, dialect: str, source_url: str | None):
        self.root = root
        self.dialect = dialect
        self.source_url = source_url
        self.r = _Reader()
        self.notes: list[str] = []
        self.out: dict = {}
        self.ident = None
        self.doi_links: list[str] = []  # DOIs found in online resources

    # -- helpers ----------------------------------------------------------

    def set(self, prop: str, value):
        if value in (None, "", [], {}):
            return
        self.out[prop] = value

    def note(self, msg: str):
        if msg not in self.notes:
            self.notes.append(msg)

    def _identifier(self, ident_el) -> dict | None:
        """MD_Identifier / RS_Identifier -> {code, codespace, href, doi}."""
        r = self.r
        code = r.text(ident_el, "code")
        href = r.href(ident_el, "code")
        space = r.text(ident_el, "codeSpace")
        authority = r.text(ident_el, "authority/CI_Citation/title")
        if not (code or href):
            return None
        return {
            "code": code or href,
            "href": href,
            "space": space,
            "authority": authority,
            "doi": _doi_url(code, href),
        }

    # -- agents -----------------------------------------------------------

    def _agent_id(self, el) -> dict | str | None:
        ident = self.r.one(el, "partyIdentifier/MD_Identifier")
        if ident is None:
            return None
        i = self._identifier(ident)
        if not i:
            return None
        url = next((u for u in (i["code"], i["href"]) if _is_url(u)), None)
        if url and "#" in url and url.split("#", 1)[1].startswith("http"):
            url = url.split("#", 1)[0]  # TERN-style "uri#uri" hrefs
        if url and not i["space"]:
            return url
        return _property_value(
            i["code"], _scheme(i["space"], i["authority"], url), url
        )

    def _email(self, el) -> str | None:
        return self.r.text(el, "contactInfo/*/address/*/electronicMailAddress")

    def _agent(
        self,
        kind: str,
        name: str | None,
        identifier=None,
        email: str | None = None,
        affiliation: dict | None = None,
        position: str | None = None,
    ) -> dict | None:
        if not (name or identifier):
            return None
        a = {"@type": [f"schema:{kind}"]}
        if name:
            a["schema:name"] = name
        if identifier:
            a["schema:identifier"] = identifier
        if position and kind == "Person":
            a["schema:description"] = position
        # CDIF allows schema:contactPoint on Person only.
        if email and kind == "Person":
            a["schema:contactPoint"] = {
                "@type": ["schema:ContactPoint"],
                "schema:email": email,
            }
        if affiliation:
            a["schema:affiliation"] = affiliation
        return a

    def _responsibility(self, el) -> tuple[str | None, str | None, list[dict]]:
        """CI_Responsibility (19115-3) or CI_ResponsibleParty (19139) ->
        (role code, role code list URI, [agents])."""
        r = self.r
        role_el = r.one(el, "role/*")
        role = _codevalue(role_el)
        codelist = role_el.get("codeList") if role_el is not None else None
        agents = []
        if _ln(el) == "CI_ResponsibleParty":  # ISO 19139
            person = r.text(el, "individualName")
            org = r.text(el, "organisationName")
            email = self._email(el)
            position = r.text(el, "positionName")
            if person:
                aff = self._agent("Organization", org) if org else None
                agents.append(
                    self._agent("Person", person, None, email, aff, position)
                )
            elif org:
                agents.append(self._agent("Organization", org, None, email))
        else:  # ISO 19115-3
            for party in r.all(el, "party/*"):
                if _ln(party) == "CI_Organisation":
                    org = self._agent(
                        "Organization",
                        r.text(party, "name"),
                        self._agent_id(party),
                        self._email(party),
                    )
                    people = r.all(party, "individual/CI_Individual")
                    for p in people:
                        agents.append(
                            self._agent(
                                "Person",
                                r.text(p, "name"),
                                self._agent_id(p),
                                self._email(p),
                                org,
                                r.text(p, "positionName"),
                            )
                        )
                    if not people:
                        agents.append(org)
                elif _ln(party) == "CI_Individual":
                    agents.append(
                        self._agent(
                            "Person",
                            r.text(party, "name"),
                            self._agent_id(party),
                            self._email(party),
                            None,
                            r.text(party, "positionName"),
                        )
                    )
        return role, codelist, [a for a in agents if a]

    def _role(self, role: str, codelist: str | None) -> dict:
        term_set = (
            codelist
            if _is_url(codelist or "")
            else f"{ISO_CODELISTS}#CI_RoleCode"
        )
        return _term(role, role, term_set)

    # -- sections ---------------------------------------------------------

    def identification(self):
        r = self.r
        idents = r.all(self.root, "identificationInfo/*")
        data = [i for i in idents if _ln(i) == "MD_DataIdentification"]
        if not data:
            kinds = sorted({_ln(i) for i in idents}) or ["none"]
            raise _OutOfScope(
                f"No MD_DataIdentification (found: {', '.join(kinds)}). "
                f"CDIF Discovery describes datasets."
            )
        if len(data) > 1:
            self.note(
                f"{len(data)} MD_DataIdentification sections; only the "
                f"first was crosswalked."
            )
        self.ident = data[0]

    def scope(self) -> str:
        r = self.r
        path = (
            "metadataScope/MD_MetadataScope/resourceScope"
            if self.dialect == "iso19115-3"
            else "hierarchyLevel"
        )
        scope = r.code(self.root, path)
        if not scope:
            self.note(
                "No resource scope (MD_ScopeCode) given; treated as dataset."
            )
            return "dataset"
        if scope not in DATASET_SCOPES:
            raise _OutOfScope(
                f"Resource scope is '{scope}'. CDIF Discovery describes "
                f"datasets (scopes: {', '.join(sorted(DATASET_SCOPES))})."
            )
        if scope != "dataset":
            self.note(
                f"Resource scope '{scope}' crosswalked as schema:Dataset."
            )
        return scope

    def metadata_identifier(self) -> str | None:
        if self.dialect == "iso19115-3":
            return self.r.text(
                self.root, "metadataIdentifier/MD_Identifier/code"
            )
        return self.r.text(self.root, "fileIdentifier")

    def metadata_date(self) -> str | None:
        r = self.r
        if self.dialect == "iso19139":
            return _iso_date(r.text(self.root, "dateStamp"))
        dates = {}
        for d in r.all(self.root, "dateInfo/CI_Date"):
            dates.setdefault(
                r.code(d, "dateType/*"), _iso_date(r.text(d, "date"))
            )
        return (
            dates.get("revision")
            or dates.get("lastUpdate")
            or dates.get("creation")
            or next(iter(dates.values()), None)
        )

    def citation(self):
        r, cit = self.r, self.r.one(self.ident, "citation/CI_Citation")
        if cit is None:
            self.note("No resource citation (title, dates, identifiers).")
            return {}
        self.set("schema:name", r.text(cit, "title"))
        alt = r.texts(cit, "alternateTitle")
        self.set("schema:alternateName", alt[0] if len(alt) == 1 else alt)
        self.set("schema:version", r.text(cit, "edition"))

        dates: dict[str, list[str]] = {}
        for d in r.all(cit, "date/CI_Date"):
            kind, value = r.code(d, "dateType/*"), _iso_date(r.text(d, "date"))
            if kind and value:
                dates.setdefault(kind, []).append(value)
        self.set(
            "schema:dateCreated", min(dates.get("creation", []), default=None)
        )
        self.set(
            "schema:datePublished",
            min(dates.get("publication", []), default=None),
        )
        modified = max(
            dates.get("revision", []) + dates.get("lastUpdate", []),
            default=None,
        )
        if modified:
            self.set("schema:dateModified", modified)

        ids = [
            i
            for el in r.all(cit, "identifier/*")
            if (i := self._identifier(el))
        ]
        return {"ids": ids, "dates": dates, "cit": cit}

    def identifiers(self, ids: list[dict], landing: str | None):
        """@id, schema:identifier and schema:sameAs."""

        def as_value(i):
            if i["doi"]:
                return _property_value(
                    i["doi"].removeprefix("https://doi.org/"),
                    "https://doi.org",
                    i["doi"],
                )
            url = next((u for u in (i["href"], i["code"]) if _is_url(u)), None)
            return _property_value(
                i["code"], _scheme(i["space"], i["authority"], url), url
            )

        if not any(i["doi"] for i in ids) and self.doi_links:
            doi = self.doi_links[0]
            ids = [
                {
                    "code": doi,
                    "href": doi,
                    "space": None,
                    "authority": None,
                    "doi": doi,
                },
                *ids,
            ]
            self.note(
                "DOI taken from an online resource link (e.g. protocol "
                "'DOI'), not from the citation identifiers."
            )
        primary = (
            next((i for i in ids if i["doi"]), None)
            or next(
                (i for i in ids if _is_url(i["href"]) or _is_url(i["code"])),
                None,
            )
            or (ids[0] if ids else None)
        )
        md_id = self.metadata_identifier()
        if primary:
            self.set("schema:identifier", as_value(primary))
            others = [as_value(i) for i in ids if i is not primary]
            self.set("schema:sameAs", _dedupe(others))
            res_id = primary["doi"] or next(
                (u for u in (primary["href"], primary["code"]) if _is_url(u)),
                None,
            )
        elif md_id:
            space = (
                self.r.text(
                    self.root, "metadataIdentifier/MD_Identifier/codeSpace"
                )
                if self.dialect == "iso19115-3"
                else None
            )
            self.set(
                "schema:identifier",
                _property_value(
                    md_id,
                    _scheme(space, None, None) or "metadata record identifier",
                ),
            )
            self.note(
                "The resource has no identifier; schema:identifier is "
                "the metadata record identifier."
            )
            res_id = None
        else:
            res_id = None

        if not res_id:
            if landing:
                res_id = landing
                self.note(
                    "@id is the schema:url value (no resolvable "
                    "resource identifier)."
                )
            elif md_id and re.fullmatch(r"[0-9a-fA-F-]{32,36}", md_id):
                res_id = f"urn:uuid:{md_id}"
                self.note(
                    "@id is urn:uuid of the metadata identifier (no "
                    "resolvable resource identifier or landing page)."
                )
            else:
                res_id = "#resource"
                self.note("No usable identifier for @id; used '#resource'.")
        self.out["@id"] = res_id

    def dates_fallback(self, dates: dict):
        if "schema:dateModified" in self.out:
            return
        md = self.metadata_date()
        for value, why in (
            (md, "the metadata record's date"),
            (
                min(dates.get("publication", []), default=None),
                "the publication date",
            ),
            (
                min(dates.get("creation", []), default=None),
                "the creation date",
            ),
        ):
            if value:
                self.set("schema:dateModified", value)
                self.note(
                    f"No resource revision date; schema:dateModified is {why}."
                )
                return

    def description(self):
        self.set(
            "schema:description", _plain(self.r.text(self.ident, "abstract"))
        )

    def language(self):
        r = self.r
        lang = (
            r.code(self.ident, "defaultLocale/PT_Locale/language")
            if self.dialect == "iso19115-3"
            else r.code(self.ident, "language")
        )
        self.set("schema:inLanguage", lang)

    def parties(self, cit):
        r = self.r
        creators, contributors, publishers, funders, pis = [], [], [], [], []
        resps = (
            r.all(cit, "citedResponsibleParty/*") if cit is not None else []
        ) + r.all(self.ident, "pointOfContact/*")
        for el in resps:
            role, codelist, agents = self._responsibility(el)
            role = role or "unspecified"
            if role in CREATOR_ROLES:
                creators += agents
            elif role in PUBLISHER_ROLES:
                publishers += agents
            elif role in FUNDER_ROLES:
                funders += agents
            else:
                if role == "principalInvestigator":
                    pis += agents
                contributors += [
                    {
                        "@type": ["schema:Role"],
                        "schema:roleName": self._role(role, codelist),
                        "schema:contributor": a,
                    }
                    for a in agents
                ]
        if not creators and pis:
            creators = pis
            self.note(
                "No author or originator; principal investigators used "
                "as creators."
            )
        if creators:
            self.set("schema:creator", {"@list": _dedupe(creators)})
        if publishers:
            self.set("schema:publisher", publishers[0])
        self.set("schema:contributor", _dedupe(contributors))
        self.set(
            "schema:funding",
            _dedupe(
                [
                    {"@type": ["schema:MonetaryGrant"], "schema:funder": a}
                    for a in funders
                ]
            ),
        )
        if any("schema:identifier" not in a for a in funders):
            self.note(
                "A funder has no identifier. CDIF's validation frame "
                "only embeds funders that have schema:identifier, so "
                "the JSON Schema check will report schema:funder as "
                "missing although it is present."
            )

        providers = []
        for el in r.all(self.root, "distributionInfo//distributorContact/*"):
            providers += self._responsibility(el)[2]
        self.set("schema:provider", _dedupe(providers))

    def keywords(self):
        r = self.r
        out = []
        for block in r.all(self.ident, "descriptiveKeywords/MD_Keywords"):
            th = r.one(block, "thesaurusName/CI_Citation")
            th_title = r.text(th, "title") if th is not None else None
            th_url = None
            if th is not None:
                th_url = (
                    r.href(th, "title")
                    or next(
                        (
                            u
                            for u in r.texts(th, "onlineResource/*/linkage")
                            if _is_url(u)
                        ),
                        None,
                    )
                    or next(
                        (
                            i["href"] or i["code"]
                            for el in r.all(th, "identifier/*")
                            if (i := self._identifier(el))
                            and _is_url(i["href"] or i["code"])
                        ),
                        None,
                    )
                )
            term_set = th_url or th_title
            for kw in r.all(block, "keyword"):
                name, href = _value(kw), _href(kw)
                if not (name or href):
                    continue
                if href or term_set:
                    out.append(_term(name, None, term_set, href))
                else:
                    out.append(name)
        for tc in r.texts(self.ident, "topicCategory/*"):
            out.append(_term(tc, tc, f"{ISO_CODELISTS}#MD_TopicCategoryCode"))
        self.set("schema:keywords", _dedupe(out))

    def spatial(self):
        r = self.r
        places = []
        for ext in r.all(self.ident, "extent/EX_Extent"):
            desc = r.text(ext, "description")
            polygons = []
            for ge in r.all(ext, "geographicElement/*"):
                kind = _ln(ge)
                if kind == "EX_GeographicBoundingBox":
                    box = self._box(ge)
                    if box:
                        places.append(self._place(desc, box))
                elif kind == "EX_BoundingPolygon":
                    polygons += [
                        p
                        for g in r.all(ge, "polygon//Polygon")
                        if (p := self._polygon(g))
                    ]
                elif kind == "EX_GeographicDescription":
                    code = r.text(ge, "geographicIdentifier/*/code")
                    if code:
                        places.append(
                            {"@type": ["schema:Place"], "schema:name": code}
                        )
            if polygons:
                places.append(self._polygon_place(desc, polygons))
        self.set("schema:spatialCoverage", places)

    def _box(self, ge) -> str | None:
        r = self.r
        try:
            w, e, s, n = (
                float(r.text(ge, f"{side}"))
                for side in (
                    "westBoundLongitude",
                    "eastBoundLongitude",
                    "southBoundLatitude",
                    "northBoundLatitude",
                )
            )
        except (TypeError, ValueError):
            self.note(
                "A bounding box with missing or non-numeric bounds was "
                "skipped."
            )
            return None
        if s > n:
            s, n = n, s
            self.note("A bounding box had south > north; swapped.")
        if w > e:
            self.note(
                "A bounding box crosses the antimeridian (west > east); "
                "kept as given."
            )
        return " ".join(_num(v) for v in (s, w, n, e))

    @staticmethod
    def _place(name: str | None, box: str) -> dict:
        p = {"@type": ["schema:Place"]}
        if name:
            p["schema:name"] = name
        p["schema:geo"] = {"@type": ["schema:GeoShape"], "schema:box": box}
        return p

    def _polygon(self, poly) -> list[tuple[float, float]] | None:
        """gml:Polygon exterior ring -> [(lon, lat), ...] in CRS84 order."""
        r = self.r
        ring = r.one(poly, "exterior/LinearRing")
        if ring is None:
            return None
        raw = r.text(ring, "posList") or " ".join(r.texts(ring, "pos"))
        if not raw:
            coords = r.text(ring, "coordinates")  # GML 2 style "x,y x,y"
            raw = coords.replace(",", " ") if coords else ""
        try:
            nums = [float(v) for v in raw.split()]
        except ValueError:
            self.note("A polygon with non-numeric coordinates was skipped.")
            return None
        if len(nums) < 6 or len(nums) % 2:
            return None
        pts = list(zip(nums[0::2], nums[1::2], strict=True))
        srs = next(
            (
                a.get("srsName")
                for a in [poly, *poly.iterancestors()]
                if a.get("srsName")
            ),
            "",
        )
        if any(abs(x) > 90 for x, _ in pts):
            lonlat = True
        elif any(abs(y) > 90 for _, y in pts):
            lonlat = False
        else:  # ambiguous: EPSG URN/URL forms are latitude-first
            lonlat = not re.search(
                r"(EPSG::|EPSG/0/)(4326|4283|7844|4258)", srs
            )
            self.note(
                f"Polygon axis order assumed "
                f"{'lon/lat' if lonlat else 'lat/lon'} from srsName "
                f"'{srs or 'none'}'."
            )
        if not lonlat:
            pts = [(y, x) for x, y in pts]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts

    def _polygon_place(self, name, polygons) -> dict:
        lons = [x for p in polygons for x, _ in p]
        lats = [y for p in polygons for _, y in p]
        box = " ".join(
            _num(v) for v in (min(lats), min(lons), max(lats), max(lons))
        )
        rings = [
            "((" + ", ".join(f"{_num(x)} {_num(y)}" for x, y in p) + "))"
            for p in polygons
        ]
        if len(rings) == 1:
            wkt, sf = f"POLYGON{rings[0]}", "sf:Polygon"
        else:
            wkt, sf = f"MULTIPOLYGON({', '.join(rings)})", "sf:MultiPolygon"
        place = self._place(name, box)
        place["geosparql:hasGeometry"] = {
            "@type": [sf],
            # A string @type: JSON-LD forbids an array on a value object,
            # although CDIF's schema and complete example use one.
            "geosparql:asWKT": {
                "@type": "geosparql:wktLiteral",
                "@value": wkt,
            },
            "geosparql:crs": {"@id": CRS84},
        }
        return place

    def temporal(self):
        r = self.r
        out = []

        def pos(el):
            if el is None:
                return None
            v = _value(el)
            if v:
                return v
            if el.get("indeterminatePosition") in ("now", "unknown", "after"):
                return ".."
            return None

        for t in r.all(
            self.ident, "extent/EX_Extent/temporalElement/*/extent/*"
        ):
            if _ln(t) == "TimePeriod":
                start = pos(r.one(t, "beginPosition")) or pos(
                    r.one(t, "begin/TimeInstant/timePosition")
                )
                end = pos(r.one(t, "endPosition")) or pos(
                    r.one(t, "end/TimeInstant/timePosition")
                )
                if start or end:
                    out.append(f"{start or '..'}/{end or '..'}")
            elif _ln(t) == "TimeInstant":
                v = pos(r.one(t, "timePosition"))
                if v and v != "..":
                    out.append(v)
        self.set("schema:temporalCoverage", _dedupe(out))

    # Licences ------------------------------------------------------------

    _CC_URL = re.compile(
        r"https?://(?:www\.)?creativecommons\.org/"
        r"(licenses|publicdomain)/([a-z\-]+)/(\d\.\d)",
        re.IGNORECASE,
    )
    _CC_BUTTON = re.compile(
        r"(?:licensebuttons\.net|i\.creativecommons\.org)"
        r"/(?:l|p)/([a-z\-]+)/(\d\.\d)",
        re.IGNORECASE,
    )
    _CC_SHORT = re.compile(
        r"\bCC[\s\-_]?(BY(?:[\s\-_](?:NC|SA|ND)){0,2})"
        r"[\s\-_]*(?:International[\s\-_]*)?(\d\.\d)?",
        re.IGNORECASE,
    )
    _CC_LONG = re.compile(
        r"Creative\s+Commons\s+Attribution"
        r"((?:[\s\-]+(?:Non-?Commercial|Share-?Alike|"
        r"No-?Deriv(?:atives|s)))*)[\s\-]+(\d\.\d)",
        re.IGNORECASE,
    )
    _CC0 = re.compile(r"\bCC0\b|Creative\s+Commons\s+Zero", re.IGNORECASE)
    _OTHER_LICENCE = re.compile(
        r"https?://(?:www\.)?(?:opensource\.org/licenses?|"
        r"spdx\.org/licenses|opendatacommons\.org/licenses)"
        r"/[\w.\-/]+",
        re.IGNORECASE,
    )

    @staticmethod
    def _cc(kind: str, code: str, version: str) -> str:
        base = "https://creativecommons.org"
        return f"{base}/{kind.lower()}/{code.lower()}/{version}/"

    @classmethod
    def _licences_in_text(cls, text: str) -> tuple[list[str], list[str]]:
        """-> (explicit licence URIs, URIs inferred from licence names)."""
        explicit = [cls._cc(*m.groups()) for m in cls._CC_URL.finditer(text)]
        explicit += [
            m.group(0).rstrip("/.") for m in cls._OTHER_LICENCE.finditer(text)
        ]
        inferred = [
            cls._cc("licenses", *m.groups())
            for m in cls._CC_BUTTON.finditer(text)
        ]
        for m in cls._CC_SHORT.finditer(text):
            if m.group(2):
                code = re.sub(r"[\s_]+", "-", m.group(1))
                inferred.append(cls._cc("licenses", code, m.group(2)))
        for m in cls._CC_LONG.finditer(text):
            parts = ["by"]
            for word, short in (
                ("commercial", "nc"),
                ("share", "sa"),
                ("deriv", "nd"),
            ):
                if word in m.group(1).lower():
                    parts.append(short)
            inferred.append(cls._cc("licenses", "-".join(parts), m.group(2)))
        if cls._CC0.search(text):
            inferred.append(
                "https://creativecommons.org/publicdomain/zero/1.0/"
            )
        return explicit, inferred

    def rights(self):
        r = self.r
        explicit, inferred, conditions = [], [], []
        for c in r.all(self.ident, "resourceConstraints/*"):
            if _ln(c) == "MD_SecurityConstraints":
                level = r.code(c, "classification/*")
                if level and level != "unclassified":
                    conditions.append(f"Security classification: {level}")
                continue
            urls = (
                r.texts(c, "reference/CI_Citation/onlineResource/*/linkage")
                + [
                    h
                    for el in r.all(c, "reference/CI_Citation/title")
                    if (h := _href(el))
                ]
                + [
                    h
                    for el in r.all(c, "otherConstraints")
                    if (h := _href(el))
                ]
                + r.texts(c, "graphic/MD_BrowseGraphic/linkage/*/linkage")
                + r.texts(c, "graphic/MD_BrowseGraphic/fileName")
            )
            ref_titles = r.texts(c, "reference/CI_Citation/title") + r.texts(
                c, "reference/CI_Citation/alternateTitle"
            )
            statements = r.texts(c, "useLimitation") + r.texts(
                c, "otherConstraints"
            )
            for u in urls:
                e, i = self._licences_in_text(u)
                explicit += e
                inferred += i
            for t in ref_titles + statements:
                e, i = self._licences_in_text(t)
                explicit += e
                inferred += i
            conditions += statements
            # Restriction codes ('license', 'otherRestrictions') add nothing
            # beyond the statements above; read them so they are not
            # reported as unmapped.
            r.all(c, "accessConstraints")
            r.all(c, "useConstraints")
        explicit = list(dict.fromkeys(explicit))
        extra = [u for u in dict.fromkeys(inferred) if u not in explicit]
        if extra:
            self.note(
                "Licence inferred from a licence name or badge image, "
                "not a licence URI: " + ", ".join(extra)
            )
        licences = explicit + extra
        self.set("schema:license", [{"@id": u} for u in licences])
        self.set("schema:conditionsOfAccess", list(dict.fromkeys(conditions)))

    # Links ---------------------------------------------------------------

    _SERVICE_NAMES: ClassVar[dict[str, str]] = {
        "WMS": "OGC Web Map Service",
        "WFS": "OGC Web Feature Service",
        "WCS": "OGC Web Coverage Service",
        "WPS": "OGC Web Processing Service",
        "WMTS": "OGC Web Map Tile Service",
        "SOS": "OGC Sensor Observation Service",
        "CSW": "OGC Catalogue Service",
    }

    @staticmethod
    def _link_kind(protocol: str | None, function: str | None) -> str:
        p, f = (protocol or "").upper(), (function or "").lower()
        if (
            "METADATA-URL" in p
            or p.startswith("IMAGE/")
            or f == "browsegraphic"
        ):
            return "skip"
        if p == "DOI":
            return "doi"
        if (
            p.startswith(
                ("OGC:", "OGC ", "ESRI:", "OPENDAP", "IMOS:AGGREGATION")
            )
            or "THREDDS" in p
        ):
            return "api"
        if "DOWNLOAD" in p or f in ("download", "fileaccess"):
            return "download"
        return "link"

    def _terms_of_service(self) -> str | None:
        """ISO has no per-link terms of service, but CDIF requires one on
        every WebAPI. Use the resource licence, else its access conditions."""
        lic = self.out.get("schema:license")
        if lic:
            return lic[0]["@id"]
        cond = self.out.get("schema:conditionsOfAccess")
        return cond[0] if cond else None

    def _webapi(self, url, protocol, name, desc) -> dict:
        m = re.match(r"OGC:([A-Z]+)", protocol or "", re.IGNORECASE)
        svc = m.group(1).upper() if m else None
        api = {
            "@type": ["schema:WebAPI"],
            "schema:serviceType": _term(
                self._SERVICE_NAMES.get(svc, protocol), protocol
            ),
        }
        if desc or name:
            api["schema:description"] = desc or name
        tos = self._terms_of_service()
        if tos:
            api["schema:termsOfService"] = tos
            self.note(
                "WebAPI termsOfService is the resource licence or "
                "access conditions (ISO has no per-service terms)."
            )
        else:
            self.note(
                "WebAPI distributions have no termsOfService: the "
                "record states no licence or access conditions."
            )
        action = {
            "@type": ["schema:Action"],
            "schema:target": {
                "@type": ["schema:EntryPoint"],
                "schema:urlTemplate": url,
                "schema:httpMethod": ["GET"],
            },
        }
        if name:
            action["schema:name"] = name  # e.g. the OGC layer name
        api["schema:potentialAction"] = [action]
        return api

    @staticmethod
    def _link(url, name, relationship) -> dict:
        target = {"@type": ["schema:EntryPoint"], "schema:url": url}
        if name:
            target["schema:name"] = name
        return {
            "@type": ["schema:LinkRole"],
            "schema:linkRelationship": relationship,
            "schema:target": target,
        }

    def links(self, cit, ids) -> str | None:
        """distribution, relatedLink and url. Returns the landing page."""
        r = self.r
        dists, related = [], []
        # Scan without the tracking reader, so that only the DOI links used
        # (not every logo or graphic link) count as mapped.
        for res in _xp("//CI_OnlineResource")(self.root):
            url = _value(next(iter(_xp("linkage")(res)), None))
            protocol = _value(next(iter(_xp("protocol")(res)), None))
            is_doi = (protocol or "").upper() == "DOI" or re.match(
                r"https?://(dx\.)?doi\.org/10\.", url or ""
            )
            if is_doi and (doi := _doi_url(url)):
                self.doi_links.append(doi)
                r.used.append(res)
        self.doi_links = list(dict.fromkeys(self.doi_links))
        for res in r.all(
            self.root,
            "distributionInfo//transferOptions//onLine/CI_OnlineResource",
        ):
            url = r.text(res, "linkage")
            if not _is_url(url):
                continue
            protocol, name = r.text(res, "protocol"), r.text(res, "name")
            desc, func = r.text(res, "description"), r.code(res, "function/*")
            kind = self._link_kind(protocol, func)
            if kind == "download":
                d = {
                    "@type": ["schema:DataDownload"],
                    "schema:contentUrl": url,
                }
                if name:
                    d["schema:name"] = name
                if desc:
                    d["schema:description"] = desc
                dists.append(d)
            elif kind == "api":
                dists.append(self._webapi(url, protocol, name, desc))
            elif kind == "link":
                related.append(self._link(url, name or desc, "related"))
        for doc in r.all(self.ident, "additionalDocumentation/CI_Citation"):
            title = r.text(doc, "title")
            for url in r.texts(doc, "onlineResource/*/linkage"):
                if _is_url(url):
                    related.append(self._link(url, title, "describedby"))
        self.set("schema:distribution", _dedupe(dists))
        self.set("schema:relatedLink", _dedupe(related))

        # Landing page. Citation links are often organisation home pages,
        # so only one that says it is a landing page, or whose URL contains
        # the record's identifier, counts; the rest become related links.
        landing, best = None, 0
        md_id = self.metadata_identifier()
        if cit is not None:
            for res in r.all(cit, "onlineResource/CI_OnlineResource"):
                url = r.text(res, "linkage")
                if not _is_url(url):
                    continue
                label = " ".join(
                    filter(
                        None, (r.text(res, "name"), r.text(res, "description"))
                    )
                )
                score = (
                    2
                    if "landing" in label.lower()
                    else (1 if md_id and md_id in url else 0)
                )
                if score > best:
                    landing, best = url, score
            for res in r.all(cit, "onlineResource/CI_OnlineResource"):
                url = r.text(res, "linkage")
                if _is_url(url) and url != landing:
                    related.append(
                        self._link(
                            url,
                            r.text(res, "name") or r.text(res, "description"),
                            "related",
                        )
                    )
            self.set("schema:relatedLink", _dedupe(related))
        if not landing and self.dialect == "iso19139":
            landing = next(
                (u for u in r.texts(self.root, "dataSetURI") if _is_url(u)),
                None,
            )
        if not landing:
            landing = next((i["doi"] for i in ids if i["doi"]), None) or (
                self.doi_links[0] if self.doi_links else None
            )
        if not landing:
            landing = r.text(
                self.root, "metadataLinkage/CI_OnlineResource/linkage"
            )
            if _is_url(landing):
                self.note(
                    "schema:url is the catalogue's metadata page (no "
                    "landing page or DOI)."
                )
            else:
                landing = None
        self.set("schema:url", landing)
        return landing

    def variables(self):
        r = self.r
        out = []
        dims = r.all(
            self.root, "contentInfo/MD_CoverageDescription//attribute/*"
        ) + r.all(self.root, "contentInfo/MD_CoverageDescription/dimension/*")
        for d in dims:
            code_el = r.one(d, "name/MD_Identifier/code")
            if code_el is None:
                code_el = r.one(d, "sequenceIdentifier/MemberName/aName")
            name = (
                _value(code_el)
                or r.text(d, "description")
                or r.text(d, "descriptor")
            )
            if not name:
                continue
            v = {"@type": ["schema:PropertyValue"], "schema:name": name}
            desc = r.text(d, "description") or r.text(d, "descriptor")
            if desc and desc != name:
                v["schema:description"] = desc
            if href := _href(code_el):
                v["schema:propertyID"] = href
            elif space := r.text(d, "name/MD_Identifier/codeSpace"):
                v["schema:propertyID"] = space  # e.g. CF standard names
            unit = r.one(d, "units/*")
            if unit is not None:
                if u := r.text(unit, "name"):
                    v["schema:unitText"] = u
                if u := r.text(unit, "identifier"):
                    v["schema:unitCode"] = u
            out.append(v)
        self.set("schema:variableMeasured", _dedupe(out))

    def lineage(self):
        r = self.r
        acts = []
        for lin in r.all(self.root, "resourceLineage/LI_Lineage") + r.all(
            self.root, "dataQualityInfo/DQ_DataQuality/lineage/LI_Lineage"
        ):
            act = {"@type": ["prov:Activity"]}
            if s := _plain(r.text(lin, "statement")):
                act["schema:description"] = s
            used = []
            for doc in r.all(lin, "additionalDocumentation/CI_Citation"):
                for url in r.texts(doc, "onlineResource/*/linkage"):
                    if _is_url(url):
                        e = {
                            "@type": ["prov:Entity", "schema:CreativeWork"],
                            "schema:url": url,
                        }
                        if t := r.text(doc, "title"):
                            e["schema:name"] = t
                        used.append(e)
            if used:
                act["prov:used"] = _dedupe(used)
            if len(act) > 1:
                acts.append(act)
        self.set("prov:wasGeneratedBy", acts)

    def catalog_record(self):
        r = self.r
        md_id = self.metadata_identifier()
        rec_id = r.text(self.root, "metadataLinkage/CI_OnlineResource/linkage")
        if not _is_url(rec_id):
            rec_id = self.source_url or (
                f"urn:uuid:{md_id}"
                if md_id and re.fullmatch(r"[0-9a-fA-F-]{32,36}", md_id)
                else "#metadata"
            )
        if rec_id == self.out["@id"]:
            rec_id += "#metadata"
        rec = {
            "@id": rec_id,
            "@type": ["schema:Dataset"],
            "schema:additionalType": [{"@id": "dcat:CatalogRecord"}],
            "schema:about": {"@id": self.out["@id"]},
            "dcterms:conformsTo": [{"@id": p} for p in CDIF_PROFILES],
        }
        if d := self.metadata_date():
            rec["schema:sdDatePublished"] = d
        for el in r.all(self.root, "contact/*"):
            agents = self._responsibility(el)[2]
            if agents:
                rec["schema:maintainer"] = agents[0]
                break
        self.out["schema:subjectOf"] = rec

    # -- report -----------------------------------------------------------

    def unmapped(self) -> list[dict]:
        touched = set()
        for el in self.r.used:
            touched.add(el)
            touched.update(el.iterancestors())
        sections = [
            c
            for c in self.root
            if isinstance(c.tag, str) and _ln(c) != "identificationInfo"
        ]
        sections += [c for c in self.ident if isinstance(c.tag, str)]
        counts: dict[str, int] = {}
        for s in sections:
            if s not in touched:
                name = f"{s.prefix}:{_ln(s)}" if s.prefix else _ln(s)
                counts[name] = counts.get(name, 0) + 1
        return [{"element": k, "count": v} for k, v in counts.items()]


class _OutOfScope(Exception):
    pass


def _missing_required(rec: dict) -> list[str]:
    missing = [
        p
        for p in ("schema:name", "schema:identifier", "schema:dateModified")
        if p not in rec
    ]
    if "schema:license" not in rec and "schema:conditionsOfAccess" not in rec:
        missing.append("schema:license or schema:conditionsOfAccess")
    if "schema:url" not in rec and "schema:distribution" not in rec:
        missing.append("schema:url or schema:distribution")
    return missing


ORDER = [
    "@context",
    "@id",
    "@type",
    "schema:name",
    "schema:alternateName",
    "schema:description",
    "schema:identifier",
    "schema:sameAs",
    "schema:version",
    "schema:inLanguage",
    "schema:dateCreated",
    "schema:datePublished",
    "schema:dateModified",
    "schema:url",
    "schema:license",
    "schema:conditionsOfAccess",
    "schema:creator",
    "schema:publisher",
    "schema:contributor",
    "schema:provider",
    "schema:funding",
    "schema:keywords",
    "schema:spatialCoverage",
    "schema:temporalCoverage",
    "schema:variableMeasured",
    "schema:distribution",
    "schema:relatedLink",
    "prov:wasGeneratedBy",
    "schema:subjectOf",
]


def crosswalk(xml: bytes | str, source_url: str | None = None) -> dict:
    """Crosswalk one ISO 19115-3 or ISO 19139 record to CDIF Discovery.

    Returns {"record": <CDIF JSON-LD or None>, "report": {...}}. The report
    gives the dialect and scope, the CDIF properties produced and the ISO
    elements they were copied from ("mapped"), CDIF required properties
    that could not be filled ("missing_required"), ISO sections the
    crosswalk ignored ("unmapped"), and every value that was inferred or
    taken from a fallback source ("inferences_and_fallbacks").
    "record" is None when the ISO record does not describe a dataset (e.g.
    a service record); "out_of_scope" then says why.
    """
    root = parse(xml)
    dialect = detect_dialect(root)
    b = _Builder(root, dialect, source_url)
    report = {"dialect": dialect}
    try:
        report["scope"] = b.scope()
        b.identification()
    except _OutOfScope as e:
        report["out_of_scope"] = str(e)
        return {"record": None, "report": report}

    c = b.citation()
    cit, ids, dates = c.get("cit"), c.get("ids", []), c.get("dates", {})
    b.description()
    b.language()
    b.parties(cit)
    b.keywords()
    b.spatial()
    b.temporal()
    b.rights()
    landing = b.links(cit, ids)
    b.variables()
    b.lineage()
    b.identifiers(ids, landing)
    b.dates_fallback(dates)
    b.out["@type"] = ["schema:Dataset"]
    b.out["@context"] = dict(CONTEXT)
    b.catalog_record()

    record = {k: b.out[k] for k in ORDER if k in b.out}
    record.update({k: v for k, v in b.out.items() if k not in record})
    produced = set(record)
    report.update(
        mapped=[
            {
                "cdif": m.cdif,
                "iso": m.iso19115_3 if dialect == "iso19115-3" else m.iso19139,
            }
            for m in MAPPINGS
            if m.cdif in produced
        ],
        missing_required=_missing_required(record),
        unmapped=b.unmapped(),
        inferences_and_fallbacks=b.notes,
    )
    return {"record": record, "report": report}
