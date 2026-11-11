# Shopee Manual Customer Verification — Odoo 18

Version: 18.0.6.1.1 (isolated patch against Production main 18.0.6.1.0).
The Staging feature branch has diverged: do NOT merge it into this branch blindly.
Production server deployment is a separate explicit step.

## New gate

- `motogene_shopee.enable_production_manual_so` defaults to `0` (OFF).
- Existing `motogene_shopee.enable_production_so_creation` may remain `0`.
- Existing `motogene_shopee.enable_production_fulfillment` must remain `0`.
- The Production Shop's `production_fulfillment_enabled` must remain unchecked.
- Shopee auto order preview sync remains independent and can stay ON.

## Human workflow

1. Verify that Shopee Order Preview is READY_TO_SHIP and all lines are correctly mapped.
2. Open Create Draft SO; select **Seller Centre Manual Verification**.
   The Order SN must be unique and Product Mapping complete. The quotation is
   created under the archived technical `Shopee Pending Verification` contact.
3. Do not confirm the SO. Arrange shipment **once** in Shopee Seller Centre.
4. Inspect the official order/AWB in Seller Centre, within the staff member's
   authorized access. Verify **Order SN, recipient name, full address, postcode**.
   Phone may be omitted if not shown; never fabricate it.
5. On Draft SO click **Verify Shopee Customer**. Enter exact Shopee Order SN,
   AWB number, and verified recipient details.
6. Select an Odoo contact only if name, complete address and postcode match;
   otherwise create a new recipient contact. The wizard never automatically
   changes an existing contact, and never makes a Shopee API mutation.
7. Verify Customer & AWB. Confirm the SO only after verification.
8. When Odoo creates a DO, the official tracking number is copied to the
   existing `mori_awb_no` field and marked **Arranged in Seller Centre**.
   No extra `ship_order` call is made. Download/print the shipping label from
   Seller Centre and attach it to the DO if necessary.

The API-verified workflow remains present and can be the default again when
Shopee PII approval arrives. No rollback/revert of this fallback is required.

## Safety and limitations

- Backend `sale.order.action_confirm` rejects pending technical contacts,
  pending manual SOs, and stale manual verifications.
- A SHA256 verification fingerprint includes partner identity, delivery
  address, phone and AWB. Changes after verification block Confirm.
- The unique Shopee Order SN and Preview constraints still apply.
- Review and only process orders that Seller Centre shows as shippable.
- Manual verification is a deliberate action by an Odoo System Administrator.
- Do not use Order Preview's masked API data as customer details.
- The original Production quantity multiplier limitation remains: do NOT
  create multi-box variation SOs on 18.0.6.1.1 until quantity multiplier
  fixes are separately reconciled and deployed.
- Archived placeholder contact is never a valid confirmed SO recipient.
- Existing customer matching is strict by name + complete shipping address
  + postcode and Malaysia. If it does not match, create a separate delivery
  contact rather than overwriting an unrelated customer's contact.
- This feature does not implement automatic creation of Sales Orders,
  automated SO confirmation, live shipment arrangement, or automated printing
  of Seller Centre AWBs.
- A status of `external` on a DO prevents the Shopee Odoo Arrange Shipment
  buttons from sending another request.

## Safe Production acceptance sequence

1. Verify exact pinned custom addon commit and backup Production database.
2. Deploy this isolated branch (do not merge unrelated Staging work).
3. Upgrade `motogene_shopee_integration` and confirm version 18.0.6.1.1.
4. Verify manual gate `0`, original SO gate `0`, fulfillment gate `0`,
   Production Auto Sync `1` and AWB checkbox unchecked.
5. Confirm an existing non-Shopee order can be processed normally (do not
   create test shipping orders to the live courier).
6. In Odoo shell, verify technical placeholder and new models/views exist.
7. After explicit operational approval, enable only
   `motogene_shopee.enable_production_manual_so` = `1`.
8. Use **one** existing simple, mapped, READY_TO_SHIP Shopee order after staff
   have arranged its shipment in Seller Centre. Verify the pending SO cannot
   confirm; manually verify it; only then confirm the SO and audit DO AWB.
9. If a validation fails, switch the manual SO flag back to `0`. Other
   Production flags and unrelated workflows remain untouched.

Do not share or store Shopee OAuth tokens, personal recipient data or full
AWB PDFs in GitHub issues, logs, public chats or screenshots.
