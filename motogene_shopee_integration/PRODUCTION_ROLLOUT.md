# MotoGene Shopee — Sandbox / Production rollout (Odoo 18)

Module: `motogene_shopee_integration` v18.0.6.0.0

## Separate environment configurations

Create **two separate** Shopee Shop records:
- **Sandbox**: Test Partner ID/Key, sandbox OAuth, test Shop ID.
- **Production**: Live Partner ID/Key, production OAuth, real Shop ID.

Do **not** change the environment of an authorized Shop record. Tokens and
Shop IDs are environment-specific. Use a verified HTTPS redirect origin
registered in the correct Shopee Open Platform developer app. Never paste keys
or access/refresh tokens into tickets or chats.

Production host: `https://partner.shopeemobile.com`.
Sandbox host: `https://openplatform.sandbox.test-stable.shopee.sg`.

## Production activation: four independent gates

All these `ir.config_parameter` keys default to disabled:

| Key | Meaning |
| --- | --- |
| `motogene_shopee.enable_production_connection` | Permit Production OAuth, connection test and read-only API access |
| `motogene_shopee.enable_production_auto_sync` | Permit Production automatic order preview import when the existing auto-sync cron is enabled |
| `motogene_shopee.enable_production_so_creation` | Permit creating a draft SO from a Production preview |
| `motogene_shopee.enable_production_fulfillment` | Permit live shipment-related calls **only** if the Production Shop's `production_fulfillment_enabled` checkbox is also enabled |

Sandbox `motogene_shopee.enable_test_so`, `motogene_shopee.enable_auto_order_sync`,
`motogene_shopee.enable_awb_auto_progress` and
`motogene_shopee.enable_bulk_dropoff` remain separate switches.

Keep Production SO and fulfillment gates at **0** during connection validation.
Connecting a shop does not arrange shipments or create SOs.

## Suggested staged tests

1. Confirm module upgrade and registry in staging.
2. Configure a **separate** Production Shop using approved live developer credentials, and set only `enable_production_connection=1`.
3. Verify OAuth callback and **Test Connection**, without touching live orders.
4. Verify manual preview, `buyer_user_id`, recipient fields, product mapping, price, escrow and permissions (if a suitable real order exists).
5. Approve Production draft SO creation separately. Confirm invoice reference, recipient and duplicate prevention.
6. Only after reviewing a **real, controlled** order, approve fulfillment using both global key and per-Shop checkbox. Confirm shipping parameter/availability, carrier, tracking, and official PDF against Seller Centre.
7. Test scheduled Production AWB progress, then evaluate bulk separately. The bulk *queue wizard* is still sandbox-only in this release.

Do not attempt production shipment from a staging sandbox test order.
Do not copy sandbox Shop IDs/tokens into Production. Do not enable multiple
fulfillment settings before verifying exact API permissions and courier support.

## Deployment precaution

Pin feature-branch SHA in parent Odoo.sh `staging` and test first.
Do not update parent `main` until test results are approved.
Odoo.sh Staging DB can be recreated; preserve test evidence/backups.
