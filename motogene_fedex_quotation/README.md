# MotoGene FedEx Quotation (Odoo 18)

**Development / staging-only prototype — not production-ready.**

## Included
- Operations-only sandbox Rates API request from draft Sales Order.
- Recipient country and postal code read from the Sales Order delivery contact.
- Manual weight/dimensions per carton; optional initial Odoo product-weight estimate for a single carton.
- Customs value derived from actual positive-priced goods lines; operator confirms origin country.
- Rates shown in FedEx's original currency, with returned net charge, surcharges and available delivery date.
- Rate results saved against Sales Order. Sales users can read rates but cannot call FedEx.

## Excluded / pending
- No Ship API, AWB, commercial invoice, payment acceptance or SO confirmation block.
- No production keys or production API calls.
- No MYR conversion (FedEx may return USD).
- Sandbox may not return a delivery date.
- Odoo product weights may not represent combo-choice or cartons accurately.
- All commodity lines currently share one operator-specified country of manufacture.
- Shipment-level actual carton weight is apportioned for rate estimation; this is **not** customs invoice weight.
- Free goods, fractional quantities, or complex combination packs require separate implementation.
- FedEx API responses and Odoo views must be tested in staging before release.

## Configuration (only after staging installation)
System Parameters, accessible by administrators:
- `motogene_fedex_quotation.client_id`
- `motogene_fedex_quotation.client_secret`
- `motogene_fedex_quotation.account_number`

Assign the **FedEx Quotation Operations** group to the operator who runs rate requests. Do not put credentials in GitHub, chat messages, or Odoo chatter.

## Testing
1. Confirm installation succeeds in staging and module loads without XML or ACL errors.
2. Test wizard on a draft international SO with valid recipient postal code.
3. Test manual weight/dimensions and genuine commodity value.
4. Test the returned account rates and currencies, and no-date fallback.
5. Test domestic address rejection, service-only order rejection and missing credentials.
6. Verify ordinary Sales users can read saved rates but cannot call FedEx.
7. Verify no AWB or shipment has been created.
