# MotoGene GDEX Normal — Staging candidate

Odoo 18 module: manual single & bulk normal consignments, Sandbox only, editable receiver wizard, 100 x 150 mm A6 PDF labels, retry of confirmed failures.

## Pre-deployment requirements
- Python 3.12, `requests`, `pypdf` installed through Odoo.sh build requirements.
- Existing `stock.picking` fields `x_studio_order_type` (selection value `Normal`) and `mori_awb_no` (char).
- Review Odoo groups, server-action access, company access, and credentials before installation.
- Sandbox token and subscription key must be configured in Settings.
- No production requests: environment hard locked to sandbox.

## Duplicate & timeout behavior
A separate database cursor saves an `uncertain` reservation before each external CreateConsignment call. A partial unique index limits unresolved/successful attempts to one per DO. For 401/400/403 explicit GDEX business rejection, attempt is marked failed; other ambiguous errors remain uncertain until manual investigation. No automatic retry of uncertain requests.

## Known limitations / QA gates
- Before staging install, confirm source of both required existing Odoo picking fields and review inherited view behavior.
- Validate separate-cursor reservation against Odoo.sh staging and multi-company permissions.
- Test one valid Sandbox DO, mixed Normal/COD, 20 DOs, multiple operators, gateway timeout, one bad receiver, duplicate-click and wizard retry, and that PDF barcode/QR scans.
- All label clipping assumes GDEX A6 rendering sits in the TOP-LEFT quadrant of A4. Verify against fresh label samples.
- Repeated requests to PDF endpoint can expire; preserve generated PDF attachment.
- ZIP is a CODE CANDIDATE, not a verified Odoo-installed release.
