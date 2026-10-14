# Next Order Coupons — Separate Issuance and Redemption (Odoo 18)

Status: **design / not installable**. The specification is committed on a development branch only. Do not deploy or install until technical hooks and regression tests are completed.

## Business rule
For `next_order_coupons`:
- Issue a *new* coupon only if the program is active, the source Sales Order is within the inclusive issuance date range (Asia/Kuala_Lumpur), and **all** applicable loyalty rules pass (eligible product, quantity and minimum amount).
- Explicit eligible product restrictions must NEVER become unrestricted when products or templates are archived.
- Once issuance ends, previously issued unused coupons remain redeemable within a **separate fixed redemption period**, subject to Odoo's usual balance, customer, usage and company restrictions.
- Fixed redemption end date is the same for all cards of a program. Last valid day is inclusive in Malaysia time; never silently rewrite existing cards.
- Keep other program types untouched.
- Retain the existing `loyalty.program`, `loyalty.rule`, `loyalty.reward`, and `loyalty.card` records and smart button.

## Backward compatibility / migration
- Existing programs remain in *legacy mode* unless Finance explicitly enables separate validity and configures both periods.
- Existing cards retain their current `expiration_date` by default.
- Any update of previously issued, unused coupons requires deliberate preview, explicit confirmation and a separate audited operation; do not alter redeemed/expired card history implicitly.
- Review automated action #13: it copies `loyalty.program.date_to` to `loyalty.card.expiration_date`. Its behavior must be reconciled **before** enabling separate validity. Do not simply change `date_to` to issuance end date.
- Keep the ordinary program active while redemption is valid; do not rely on archive/unarchive to stop issuance.

## Incident regression
- Program 50; loyalty rule 43; archived product variant 6843 (4th Year Anniversary — KoraGene 7 Boxes Pack).
- In active context `rule.product_ids.ids == []`; with `active_test=False`, `[6843]`.
- `any_product == True` has been observed despite an underlying explicit product relation.
- Verify actual issued coupon linkage and root causes in staging. Do not use `rule.any_product` as the only indicator that restrictions exist.
- 17 existing cards were observed, including 4 created October 13, 2026. Preserve them pending operations review.

## Required development audit before runtime hooks
1. Locate exact Odoo 18 `sale_loyalty` issuance entry point(s) for manual and website orders. Cover automatic points/card generation and order confirmation.
2. Locate coupon-redemption validation for order/website, including program `date_from`/`date_to` use.
3. Locate `loyalty.rule._get_valid_product_domain` / `_get_valid_products` and implement archived-product-safe eligibility without broadening any other rules.
4. Identify actual XML form view external ID and fields in target Odoo.sh database; implement inherited view, leaving standard UI and other program types intact.
5. Audit automations and any custom overrides in this Odoo.sh installation.
6. Implement guardrails and comprehensive tests before making the module installable.

## Acceptance test matrix
- Eligible product/quantity/amount within issuance range → exactly one intended coupon.
- Wrong product OR insufficient quantity OR insufficient amount OR outside issuance range → zero new coupons.
- Archived eligible product → zero coupons for unrelated products (even after program archive/unarchive).
- Existing unredeemed coupon redeems after issuance ended but before fixed redemption expiry.
- Redemption expires after 23:59:59 Asia/Kuala_Lumpur on fixed end date.
- Existing legacy programs and coupons unchanged until opt-in.
- Existing unused cards unchanged on setting edit unless explicit confirmed batch operation.
- No change to other Discount & Loyalty program types; multi-company, sales and website flows pass.
- Repeat confirmation, cancellation and refund/reversal must not duplicate coupon issuance.

## Deployment gates
Development branch → code review → staging install/upgrade → regression tests on cloned data → approval → production. No deployment or changes to production are authorized in this branch.
