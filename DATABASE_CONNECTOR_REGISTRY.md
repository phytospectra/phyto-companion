# Database Connector Registry — M0

Date: 2026-10-31 · Milestone M0 of `../MULTIVENDOR_LCMS_NMR_DISCOVERY_PROMPT.md`
· Machine-readable companion: `data/connector_registry.json` (same content,
schema below). Rule: **a resource is "integrated" only when its permitted
access route and usable content are verified**; nothing here is integrated yet.
`rights_status: unknown` means **no automatic bulk ingestion and no training
use** until resolved.

## Status vocabulary

- `registered` — listed with metadata; access route NOT yet verified.
- `verified-access` — documented interface confirmed reachable/usable.
- `integrated` — connector code + tests + recorded live query.
- `restricted` — authorized route required; not planned without it.
- `obsolete` / `duplicate-of` — recorded to prevent re-work.

## Connector inventory

| ID | Resource | Role | Evidence types | Access mode | Auth | Rights status | Integration status | Notes |
|---|---|---|---|---|---|---|---|---|
| pubchem | PubChem | structures, identifiers, formulas | structure, formula, synonyms | PUG-REST API | none (rate limits) | documented public interface; contribution provenance must be retained | registered | PUG-REST documented; verify terms at integration |
| chebi | ChEBI | curated chemical entities | structure, roles | API/OBO files | none | open (CC-ish; verify) | registered | — |
| coconut | COCONUT | NP candidates + reported organisms | structure, organism, reference | web + dumps | none for public data | software vs contributed-data terms differ — verify per artifact | registered | source publication/taxonomy retained; occurrence is a prior, never identification |
| lotus | LOTUS | NP structures + taxonomic occurrence | structure, taxonomy | dumps/API | none | CC BY-SA family (verify version) | registered | — |
| npatlas | Natural Products Atlas | NP structures | structure, origin | web + API? | verify | verify | registered | complementary to COCONUT |
| npass / knapsack / herb / impat / tcmid / tcmsp | Herbal/traditional families | reported ingredient-plant-disease associations | association text, structure | web/scrapes | verify each | verify each; distinguish measured chemistry from predicted targets and traditional-use text | registered | highest caution family: most are citation-backed text, not measured data |
| gnps-libraries | GNPS spectral libraries | experimental MS/MS evidence | MS/MS spectra | API/downloads | key for jobs; libraries downloadable | per-library terms; verify | registered | dedupe mirrored spectra — one observation, not three confirmations |
| massbank | MassBank | high-quality experimental MS/MS | MS/MS spectra | REST API | none | open (verify record-level attributions) | registered | — |
| mona | MoNA | experimental spectra | MS/MS, GC-MS | API | optional | verify per sub-library | registered | — |
| metlin | METLIN | reference spectra/fragments | MS/MS | verify | verify | **unknown** | restricted until verified | prompt explicitly flags: terms not established |
| mzcloud | mzCloud | reference spectra | MS/MS | commercial | license | proprietary | restricted | optional authorized connector only |
| nist-lib | NIST MS libraries | reference spectra | MS/MS, EI | license | commercial | proprietary | restricted | user-supplied licensed libraries allowed; no redistribution |
| np-mrd | NP-MRD | NMR spectra/assignments for NPs | 1D/2D NMR | web + downloads? | verify | dataset-specific terms | registered | separate measured vs predicted |
| nmrshiftdb2 | nmrshiftdb2 | NMR assignments | 1D/2D NMR | API/dump | none | open (verify) | registered | assignment quality varies; recorded |
| bmrb | BMRB | small-molecule NMR | NMR | API/files | none | open (verify) | registered | — |
| hmdb | HMDB | metabolite records | structure, spectra, bio context | downloads/API | none (academic terms) | verify commercial-use clause | registered | structure record ≠ spectral match |
| lipidmaps / swisslipids | LIPID MAPS, SwissLipids | lipid candidates | structure, MS/MS | API | none | open (verify) | registered | — |
| foodb / phenol-explorer | FooDB, Phenol-Explorer | food constituents | structure, food occurrence | web/downloads | verify | verify | registered | food occurrence ≠ current sample presence; matrix/prep/units retained |
| chembl / bindingdb / pubchem-bioassay / iuphar | Bioactivity families | targets, assays | bioactivity | API | varies | varies | registered | assay type/organism/endpoint/units/confidence retained |
| reactome / wikipathways / kegg | Pathways | pathway context | pathway membership | API/files | KEGG licensing is restricted-ish (verify) | varies | registered | uncertain compound mappings propagate into enrichment limits |
| massive / metabolights / mw | Study repositories | reference studies, validation data | raw + processed studies | API/downloads | none | per-study terms | registered | study repositories, NOT curated ID libraries |
| home-libraries | User/institution libraries | authentic standards, validated assays | everything | private upload | tenancy | private by default | registered (design) | provenance, tenant access, standard identity, method, reviewer status required |

## Federated-search contract (binding for the future API)

- Result statuses are distinct and never conflated: `no_hit`, `not_searched`,
  `access_denied`, `timeout`, `unsupported_query`. An outage can never become
  evidence of absence.
- Required connector metadata (per source): official URL, publisher,
  release/date/hash, evidence types, species/matrix scope, experimental vs
  predicted status, API/bulk/manual mode, authentication, rate/size limits,
  licence/terms + downstream restrictions, redistribution/training permission,
  attribution, health status, verified counts. Unknown rights ⇒ no automatic
  bulk ingestion and no training use.
- Structure normalization has explicit, versioned policies for salts, charge,
  tautomers, stereochemistry; originals retained; stereoisomers never collapsed;
  mirrored spectra deduplicated to one underlying observation with all mirrors
  recorded.
- Rankings carry evidence-separated tiers (mass/formula, isotope, experimental
  MS/MS, predicted fragmentation, NMR, method-compatible RT/CCS, taxonomy,
  literature) plus the list of providers queried and excluded. Taxonomy never
  eliminates a novel or unexpected compound. No spectral entry ⇒ no spectral
  confirmation even with a formula/name hit.
- Timeouts, retry/backoff, caching, incremental updates, snapshot rollback and
  per-connector tests are required before any connector is `integrated`.

## JSON schema (data/connector_registry.json)

```json
{
  "version": "2026-10-31",
  "connectors": [
    {
      "id": "string",
      "name": "string",
      "role": "string",
      "evidence_types": ["structure|ms2|nmr|bioactivity|pathway|study|occurrence"],
      "access_mode": "api|bulk|manual",
      "auth": "none|api_key|license",
      "official_url": "string|null",
      "rights_status": "documented-open|unknown|proprietary",
      "integration_status": "registered|verified-access|integrated|restricted|obsolete",
      "notes": "string"
    }
  ]
}
```
