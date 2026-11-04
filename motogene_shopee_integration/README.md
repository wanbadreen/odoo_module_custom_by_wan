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
