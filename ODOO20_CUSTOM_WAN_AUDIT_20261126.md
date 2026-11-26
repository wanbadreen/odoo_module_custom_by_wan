# Custom WAN: Odoo 18 → 20 one-pass source migration (26 Nov 2026)

**Safety:** Changes are isolated to `audit/odoo20-customwan-one-pass-20261126`. Neither `main` nor the previously tested upgrade branch is modified. No claim of Odoo 20 installation or production approval is made.

## Scope
The original inventory covers **12 functional Custom WAN modules**. Eleven are represented in this branch; the twelfth (`motogene_fedex_quotation`) is still isolated on `feature/fedex-quotation` and has its own Odoo 20 preparation branch `audit/odoo20-fedex-20261126`. The repository additionally contains `morimoto_mobile_integration_tests` (a test-only addon, not a twelfth business feature).

## Source changes made
- Converted **30 legacy ACL rows in 8 modules** to `security/ir.access.csv`, with identical granted operation sets. For Event Contacts and Activity Hub, four company-domain rules are transferred into the corresponding staff access rows. Removed obsolete rule XML and ACL CSV from this branch.
- Converted **21 legacy SQL constraints in 12 source files** to named `models.Constraint` attributes. Preserved SQL statements, messages and derived constraint identifiers.
- Updated Shopee sale lines to `product_uom_id` and Complaint/Return stock move reads to `uom_id`.
- Updated XLSX attachment creation to write `raw` bytes in Event Daily Summary and Customer Complaint/Return.
- Separate FedEx prep: 7 ACL rows converted, version changed to `20.0.1.0.0`; source stays on its separate FedEx branch.
- No Shopee production-fulfilment switches enabled; no live APIs called.

## Module-by-module readiness
| Module | Source migration status | Mandatory follow-up |
|---|---|---|
| motogene_gdex_normal | Existing Odoo 20 compatibility patch; Development UAT PASS per operator | Production-clone DO, API, printing, external Percy intercompany dependency |
| motogene_promotion_engine | Existing compatibility patch; Development functional UAT closed per operator | Automated source tests, Studio/production-clone history & role checks |
| motogene_shopee_integration | ACL + SQL constraints + SO line UoM updated | Install/registry, manual recipient verification, order mapping, duplicate prevention, AWB, offline/sandbox APIs |
| motogene_event_daily_summary | ACL + SQL constraints + attachment updated | Date/time, two XLSX sheets, skip empty days, mail queue & access |
| motogene_event_contacts | ACL + SQL constraints + multi-company rules updated | Native website registration controller signature, customer dedup, permission matrix |
| motogene_event_activity_hub | ACL + SQL constraints + company rules updated | Odoo 20 event question models, JS/OWL, session and ticket views |
| motogene_event_self_service | ACL + SQL constraints updated | Email verification, expiry, public route isolation and event cancellation |
| motogene_event_operations_bundle | ACL updated | Export binary read/write, staff email, Studio/XLSX, QWeb/website flows |
| loyalty_coupon_validity_extension | Manifest already 20.0; no source change in this batch | Loyalty issuance boundaries, expired and archived products, full addon install |
| morimoto_customer_complaint_return | ACL + SQL + stock UoM + 2 XLSX attachments updated | Studio fields, return quantity, DO/invoice links, exports and email |
| morimoto_mobile | ACL + SQL constraints updated | Public controller permissions, customer sessions/rate-limit, idempotency and delivery |
| motogene_fedex_quotation | Separate FedEx branch: manifest + ACL updated | Sandbox Rates API only; verify wizard, sender/recipient, currency/weight and roles |

## Deep Odoo 20 compatibility review — confirmed source-level fixes

The following were **verified directly against the `odoo/odoo` 20.0 source** and then changed on this isolated branch:

| Confirmed v20 contract | Affected Custom WAN path | Preserved behavior / remaining proof |
|---|---|---|
| `event.question.event_id` and `event_type_id` were replaced by `event_ids` / `event_type_ids` M2M | Activity Hub activity/question models, native event QA fixtures | Per-session private question IDs, historical answers, deduplication and default Name/Email rules; clone data migration still required |
| Website visitor helper moved from `website.visitor` to `ir.http` | Event Contacts website controller | Booking owner distinct from participant, same visitor link |
| `fields.Binary` yields `BinaryValue` raw content | Mobile catalogue images, Activity Hub cover, Events export wizard | Same PNG/JPEG/data URI and downloadable XLSX |
| `ir.attachment.raw` accepts raw bytes | Shopee single/bulk AWB; Event Daily Summary; Customer Complaint XLSX | Same PDFs, XLSX files, no duplicate AWB/API requests |
| `ir.actions.server.group_ids` replaces `groups_id` | Two Shopee bulk server actions | Admin-only contextual actions retained |
| `odoo.tools.misc` no longer exports `xlsxwriter` | Event Daily Summary, Event Operations, Customer Complaint exporter | Direct import of installed `XlsxWriter` package; worksheet layout unchanged |
| `sale.order.line.product_uom_id` / `stock.move.uom_id` | Shopee SO wizard, Complaint Return | Same product unit and returned-quantity mapping |
| Odoo 20 `ir.access.csv` + `models.Constraint` | 8 add-ons with legacy ACL, 21 SQL constraints in 12 code files | Same CRUD operation sets and SQL uniqueness intent; cross-company security and historical DB must be tested |

**Historical Activity data safety:** Added an Odoo 20 migration script for the per-session Event question marker. It never rewrites attendee responses or silently assigns an ambiguous question; it aborts if an answered old question loses its Event link. The native Odoo version-upgrade path must still be validated on a full database clone. This is a safety gate, **not a claim that historical data migration passed**.

**Field/API reference source:** `odoo/odoo` branch `20.0`:
`addons/event/models/event_question.py`,
`addons/event/models/event_event.py`,
`addons/website_event/controllers/main.py`,
`odoo/orm/fields_binary.py`,
`odoo/tools/binary.py`,
`odoo/addons/base/models/ir_actions.py`,
`odoo/tools/misc.py`,
`addons/sale/models/sale_order_line.py`,
`addons/stock/models/stock_move.py`.

## Dependency inspection

The 12 branch add-ons form an **acyclic internal dependency graph**. The first 11 are business add-ons and `morimoto_mobile_integration_tests` is a test-only addon. The separate FedEx addon is the 12th business addon, and must not be silently merged from its isolated feature branch.

| Addon or group | Required dependencies | Key caveat |
|---|---|---|
| GDEX Normal | `stock`, `delivery`, **`morimoto_inter_company`** | Intercompany module is in Percy repository and not owned by this branch; its Odoo 20 version must be verified before isolated install |
| Promotion Engine | `sale_management`, `sale_stock`, `product` | Existing Development UAT PASS, freeze business rules |
| Shopee | `base`, `web`, `product`, `sale`, `sale_stock` | Sandbox/production security switches remain unchanged |
| Activity Hub | `website_event` | Event question M2M migration is a key clone gate |
| Event Contacts | Activity Hub, Self-Service, `website_event`, `mail` | Install only after Activity Hub + Self-Service |
| Daily Summary | `website_event`, `mail` | Confirm 2-sheet XLSX and mail queue |
| Event Self-Service | `website_event`, `mail` | Public verification and cancellation scope |
| Operations Bundle | All four Events addons above | Install last among Events |
| Loyalty Coupon Validity | `sale_loyalty` | Existing Odoo 20 hooks verified to exist; test issuance date parity |
| Complaint & Return | `base`, `mail`, `sale`, `account`, `stock` | Studio-specific fields and report outputs |
| Mobile | `sale_management`, `mail`, `delivery` | Host/database guards intentionally retained |
| Mobile integration tests | Mobile, Promotion Engine | Do not install as a business feature |
| FedEx Quotation (separate branch) | `sale_management`, `stock`, `delivery` | Sandbox rates only; no live shipping |

## Regression acceptance matrix (must be executed, not inferred from source)

| Case | Area | Mandatory parity evidence |
|---|---|---|
| CW20-01 | Fresh Odoo 20 registry | Correct install order, models, constraints, groups, assets, cron and inherited views; no tracebacks |
| CW20-02 | Production clone upgrade | All legacy events, answers, SO/DO, AWBs, contacts, histories, attachments and Studio fields retained |
| CW20-03 | Security matrix | Admin / Sales / CS / Warehouse / Events staff / Public; forbidden cross-company access blocked |
| CW20-04 | Shopee import | Preview dedup, exact mapped product and price, masked address/manual verification, income and invoice reference unchanged |
| CW20-05 | Shopee logistics | Single AWB, bulk merge, no duplicate `ship_order`, uncertain response does not auto-retry; PDFs parse and scan |
| CW20-06 | Event Activity & booking | One parent Activity, repeated session sync idempotent, no duplicated Name/Email, correct owner/contact mapping |
| CW20-07 | Historical answers | Changing used answer rejected; unused answer editable; answers and Event question IDs retained on upgraded clone |
| CW20-08 | Event cancellation | Guest verification token one-time and company/website scoped; cancellation only before start |
| CW20-09 | Daily summary & exports | No-new-attendee skip, exact two XLSX sheets, timezone boundary, date filters, email dedup |
| CW20-10 | Complaint/Return | SO/DO/invoice link, delivered/returned qty/UoM, Studio selections, two XLSX exports |
| CW20-11 | Mobile | Product images, authentication, idempotent quotation, duplicate account/session, host/database guards, discounts |
| CW20-12 | Coupon validity | Before/after issuance window, expiry, archived products, other loyalty unaffected |
| CW20-13 | GDEX | Existing Development PASS frozen; retest on Percy-compatible upgraded clone, avoid live API smoke testing |
| CW20-14 | FedEx | Sandbox rate API, quote amounts/currency, explicit dimensions/weights, roles, no shipment creation |

**Stop criteria:** Any loss/change of historical attendee answer, serial, original SO/DO linkage, cross-company isolation, or unsafe external shipment action blocks approval. No change may be merged to `main` based only on static checks.

## Testing order / gates
1. Run `python3 tools/odoo20_custom_wan_preflight.py` in the batch branch, then inspect errors.
2. Build isolated Odoo 20 database; upgrade/install modules in dependency order. `morimoto_inter_company` belongs to Percy and must be ported/available for GDEX.
3. Test access matrix: Admin, CS, Event Staff, Warehouse, Customer, and multi-company isolation. Security CSV compilation is not sufficient for parity.
4. Validate core SO/DO/reward/return/website/XLSX operations with deterministic test data. Run source test suites and investigate any Odoo 20 API errors.
5. Validate Shopee and FedEx only through approved test/sandbox integrations; never auto-send production shipments as a smoke test.
6. Rehearse on an upgraded anonymized production DB clone including Studio fields, old serials and workflow history; obtain separate deployment sign-off.

**Open risk:** The Odoo 20 security model may differ from Odoo 18 when multiple access groups overlap. The generated company-domain rows need two-company user tests before approval. Odoo 20 website inheritance, external integration contracts and upgraded production data are not verified by this source patch.
