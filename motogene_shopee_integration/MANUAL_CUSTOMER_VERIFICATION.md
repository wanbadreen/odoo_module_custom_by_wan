# MotoGene Shopee — Simple Manual Customer Assignment (Odoo 18)

Version: `18.0.6.1.2`. Patch on deployed Production `18.0.6.1.1`
(feature branch `feature/shopee-manual-prod-isolated-20261111`).

This patch keeps the API-based flow, Production Auto Sync and existing Odoo
fulfillment protections. It removes the unnecessarily strict duplicate Seller
Centre name/address input and string comparison.

## Intended workflow

1. Shopee order is imported as an Order Preview by the existing auto-sync job.
2. For a mapped, `READY_TO_SHIP` preview, create a **draft** with the
   `Select Customer Manually` mode. Never confirm the placeholder draft.
3. Staff arrange shipment **once** in Shopee Seller Centre and inspect
   the actual order, recipient and delivery address there.
4. Click **Assign / Change Shopee Customer** on the draft.
5. Enter the Seller Centre **AWB/tracking number**.
6. Choose:
   - **Select Existing Odoo Contact**: staff visually compare the contact's
     name AND actual delivery address with Seller Centre, then select it.
     There is **no extra Seller Centre address field** or automatic fuzzy
     matching. Odoo refuses inactive/placeholder/wrong-company or incomplete
     Malaysian addresses.
   - **Create New Odoo Contact**: when no appropriate contact exists,
     enter the genuine name, full delivery address and 5-digit postcode.
     Phone is optional if not available; never invent or copy masked values.
7. Click **Assign Customer & AWB**. The draft receives the contact as customer,
   invoice and delivery contact. Odoo records the staff member/time and
   saves a fingerprint of the selected contact and AWB.
8. Confirm SO only after the staff checks the details. A confirmed SO creates
   DO(s) and copies Seller Centre AWB to `mori_awb_no`, with shipment state
   `external`. No `ship_order` or another shipment request is sent.

The wizard no longer asks staff to type Order SN, recipient name and address
for **existing** contacts, or tick a second verification checkbox.
The source of the truth remains the authorized Seller Centre view.

## Protection retained

- Only System Administrators can use manual assignment.
- `motogene_shopee.enable_production_manual_so` remains an independent flag,
  and is `1` only if an operator explicitly enabled it.
- `motogene_shopee.enable_production_so_creation` stays `0`.
- `motogene_shopee.enable_production_fulfillment` stays `0`, with
  the Production Shop live shipment checkbox unchecked.
- `action_confirm` rejects a pending placeholder or a missing/stale AWB
  fingerprint, and checks saved delivery contact name/address/postcode/MY.
- One SO per Shopee Order SN, and one AWB cannot be assigned to a different SO
  through the wizard.
- Reassigning a different customer or changing the saved contact details after
  assignment requires the draft to be assigned again before confirmation.
- Product mapping and status restrictions in the original Draft wizard remain.
- Staff must visually verify *the correct physical delivery address*. Matching
  names or postcodes alone is **not** sufficient. This workflow intentionally
  trusts an authorized human review; it does not perform automatic matching.
- Odoo does not arrange shipments for these orders. Use the official AWB
  from Seller Centre.

## Compatibility and deployment

Existing previously verified SOs (e.g. the already confirmed manual workflow)
retain their `verified` status and AWB; field keys and database records have
not been renamed. Only their user-facing labels change.

The `main` branch in the *custom addon* repository is not necessarily the
deployed branch: the previous custom-main push was denied with HTTP 403.
The parent Production submodule was pinned to an isolated Shopee commit.
Deploy by pinning the new isolated branch commit **in the parent repository**
after verifying branch state, **not** by merging an unrelated staging branch.

This is *not* the Staging `18.0.6.3.0` quantity-multiplier / sync-retry build.
Do not use multi-box product variations in this `18.0.6.1.2` flow until those
fixes have been reconciled separately.

Review the updated GitHub diff, snapshot/back up the Production database,
deploy/pin the commit and upgrade `motogene_shopee_integration`.
Check that Auto Sync is still on, while automatic SO/fulfillment are off.
Only exercise a single-box production draft with a legitimately arranged
Seller Centre AWB and staff-checked customer details. Do not repeat a live
shipment arrangement.
