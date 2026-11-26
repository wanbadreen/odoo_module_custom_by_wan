# Custom WAN: Odoo 18 → 20 one-pass source migration (26 Nov 2026)

**Safety:** Changes are isolated to `audit/odoo20-customwan-one-pass-20261126`. Neither `main` nor the previously tested upgrade branch is modified. No claim of Odoo 20 installation or production approval is made.

## Scope
The original inventory covers **12 functional Custom WAN modules**. Eleven are represented in this branch; the twelfth (`motogene_fedex_quotation`) is still isolated on `feature/fedex-quotation` and has its own Odoo 20 preparation branch `audit/odoo20-fedex-20261126`. The repository additionally contains `morimoto_mobile_integration_tests` (a test-only addon, not a twelfth business feature).

## Source changes made
- Converted **30 legacy ACL rows in 8 modules** to `security/ir.access.csv`, with identical granted operation sets. For Event Contacts and Activity Hub, four company-domain rules are transferred into the corresponding staff access rows. Removed obsolete rule XML and ACL CSV from this branch.
- Converted **20 legacy SQL constraints in 12 source files** to named `models.Constraint` attributes. Preserved SQL statements, messages and derived constraint identifiers.
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

## Testing order / gates
1. Run `python3 tools/odoo20_custom_wan_preflight.py` in the batch branch, then inspect errors.
2. Build isolated Odoo 20 database; upgrade/install modules in dependency order. `morimoto_inter_company` belongs to Percy and must be ported/available for GDEX.
3. Test access matrix: Admin, CS, Event Staff, Warehouse, Customer, and multi-company isolation. Security CSV compilation is not sufficient for parity.
4. Validate core SO/DO/reward/return/website/XLSX operations with deterministic test data. Run source test suites and investigate any Odoo 20 API errors.
5. Validate Shopee and FedEx only through approved test/sandbox integrations; never auto-send production shipments as a smoke test.
6. Rehearse on an upgraded anonymized production DB clone including Studio fields, old serials and workflow history; obtain separate deployment sign-off.

**Open risk:** The Odoo 20 security model may differ from Odoo 18 when multiple access groups overlap. The generated company-domain rows need two-company user tests before approval. Odoo 20 website inheritance, external integration contracts and upgraded production data are not verified by this source patch.
