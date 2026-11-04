# MotoGene Shopee Integration — OAuth Foundation

Odoo 18 Phase 1 only. Sandbox mode is enforced for OAuth and test API requests. No sales, invoices, stock, shipping or AWB are created.

## Deploy
1. Install module from custom addon branch in **Odoo.sh staging only**.
2. Go to Shopee Integration > Shopee Shops as Odoo administrator.
3. Create a record; select Sandbox; supply the **test** Partner ID/Key (do not commit secrets).
4. Set Redirect Base URL to the exact HTTPS staging origin, without trailing path.
5. Shopee Open Platform Test Redirect URL Domain must agree with that origin.
6. Save; click Connect Shopee (Sandbox); authorize the sandbox shop.
7. Click Test Connection.
8. Enable the token refresh Scheduled Action only after a successful test.

## Notes
- Reset the previously exposed test partner key.
- Development/staging domain changes invalidate the redirect configuration.
- Re-authorization is necessary if the Shopee grant or refresh token expires.
- Verify Shopee current API contract and sandbox permissions before invoking OAuth.
- All tokens are stored in Odoo database restricted to System admins; DB administrators still have access. For stronger protection use a dedicated secrets store.
- Callback uses one-time nonce embedded in path; do not log request URLs containing auth codes.
- Do NOT deploy this feature branch into the parent production repository.

## Phase 2: read-only product mapping & order preview

- `Shopee Integration > Product Mapping`: explicitly map a sandbox `item_id` and `model_id` (use `0` for unvarianted items) to an Odoo product variant.
- `Shopee Integration > Shopee Shops > Preview Last 3 Days Orders (Sandbox)`: fetch up to three pages / 150 order references, then retrieve details and persist only the order number, status, time, totals, and item overview.
- `Shopee Integration > Order Previews`: inspect mapping state and Shopee order item prices.
- No customer personal data, buyer addresses, raw API JSON, Sales Orders, invoices, delivery orders, inventory movements, or AWBs are created.
- Re-running the import updates the same order via unique shop + order SN; any unmatched item is flagged Review Required.
- API failures should surface as an Odoo UserError. Avoid copying raw URLs or tokens into support screenshots.

### Staging test cases
1. Upgrade module in Odoo staging and verify menus and installed models.
2. Run preview without sandbox orders: empty table, no Sales Orders created.
3. Create a sandbox test order via Shopee Open Platform > Test Order, authorize the same sandbox shop, then preview.
4. Confirm order SN/status/total/lines; compare listed item prices with Shopee sandbox.
5. Add a mapping with the exact item ID and model ID; re-run preview to confirm Matched.
6. Run preview again: still one record per shop + order SN; no duplicate.
7. Verify product master list price unchanged and no SO/DO or invoices created.
8. Confirm no production credential or real seller data is involved.

**Deployment safety:** this branch is not for production. No automatic cron for order import is created. Any future Phase 3 SO creation requires a separately reviewed release.
