# Generated document branding and templates

## Scope

StrataOS uses one building-scoped document profile for the generated artefacts that
already exist in the application:

- levy notices and levy reminders;
- general owner notices;
- AGM invitation / meeting notice letters; and
- canonical financial report PDF exports.

The owners corporation and its managing agency are separate identities. The profile
therefore stores a separate name, logo and ABN for each. Do not copy one logo into both
fields merely to fill the header.

The 118-page reference AGM pack contains an invitation, statutory notice, disclosures,
agenda and many appended reports. StrataOS currently generates the AGM invitation and
maintains meetings/motions separately. A full pack assembler remains a separate feature:
it must order approved attachments, produce a contents page, preserve page numbering and
record the exact issued version. This change does not label that planned report complete.

## Reference-document findings

| Reference | Conventions adopted |
|---|---|
| Annual levy contribution notice | agency contact block, owners-corporation ABN, contribution period, Admin/Sinking split, instalment schedule and GST visibility |
| Levy due notice | recipient/account block, entitlement, dated line items, paid/owing totals, legal/arrears copy and payment information |
| Financial summary | restrained letterhead, strong report title, summary metrics, compact transaction table and totals |
| AGM 2026 pack | large agency identity, formal scheme/title block, meeting access details, quorum guidance, agenda, disclosures and page numbering |

Payment-provider logos, barcodes and DEFT/BPAY/Post Billpay references are transaction-
specific payment artefacts, not document-branding assets. They remain in the levy/payment
configuration and must not be uploaded as the building or agency logo.

## Settings UI

Open **Settings -> General -> Document Branding & Letterhead**.

Configure:

1. Owners Corporation / Building Logo.
2. Strata Management Logo.
3. Units Plan number and Owners Corporation ABN.
4. Managing agency company name, ABN, licence, address, phone, email and website.
5. Letterhead identity mode:
   - **Building + managing agency** (recommended);
   - **Managing agency only**; or
   - **Building only**.
6. Accent colour, document footer and optional AGM recording/insurance disclosures.

Uploads accept PNG, JPEG and WebP files up to 2 MB. SVG is intentionally excluded because
it may contain active content and is not needed for a reliable PDF letterhead.

## API and storage

- `PUT /api/settings` persists profile fields in the existing building-scoped general
  settings record.
- `POST /api/settings/document-logo/building` uploads the owners-corporation logo.
- `POST /api/settings/document-logo/strata-manager` uploads the agency logo.
- Both upload routes require the existing settings-management permission, scan the file,
  reject unsafe formats and save a generated filename below
  `FILE_STORAGE_PATH/branding/<building_id>/`.
- Public URLs are returned as `/uploads/branding/<building_id>/<generated-name>`.

The deployment must map `/uploads/` to the same persistent volume used by
`FILE_STORAGE_PATH`. Include that volume in backup/restore and ensure all app instances
share it. The database stores only the URL; losing the volume loses the rendered logo.

No database migration is required because general settings are stored as a building-
scoped JSON document/value. The Pydantic contract was extended so the new keys are no
longer discarded by `PUT /settings`.

## Generator contract

Use `services.document_branding_service.resolve_document_branding(settings, building_id)`
for any new PDF, DOCX, XLSX or HTML letter generator. Do not read the settings collection
again inside a renderer; callers should resolve settings once and pass them through.

A new generator must:

- keep `building_id` scoping on every settings read;
- render the building and agency identities according to `document_branding_mode`;
- use `document_accent_color` only after normalisation;
- resolve local images through `local_brand_asset_path()` for ReportLab;
- escape all owner-, manager- and meeting-supplied text;
- avoid downloading remote logos during a financial job;
- state source/completeness on financial exports; and
- preserve legal wording as jurisdiction-specific configuration or reviewed template
  content rather than copying a sample notice blindly.

## Full AGM pack follow-up

The pack assembler should consume the existing meeting/motion records plus explicitly
approved financial and governance attachments. Before issuance it should validate:

- meeting type, date, time, venue and join credentials;
- jurisdiction-specific quorum and voting wording;
- motion number, resolution type and explanatory text;
- current insurance, audit, budget and sinking-fund documents;
- proxy/absentee forms where applicable;
- disclosure text and privacy/recording policy;
- attachment order, contents-page links and continuous page numbering; and
- an immutable issue record containing template/profile version and attachment hashes.
