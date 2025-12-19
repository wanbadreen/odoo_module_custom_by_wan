# Morimoto GDEX Prime Integration

Integrate GDEX Prime OpenAPI with Odoo Delivery Orders to create GDEX consignments and sync shipment status.

## Setup

1. Install the module **Morimoto GDEX Prime Integration**.
2. Go to **Settings → Inventory** and configure:
   - GDEX Account No
   - GDEX Environment (Sandbox/Demo or Production)
   - GDEX API Tokens
   - Optional base URL override
3. Open an **Outgoing** Delivery Order and click **Create GDEX AWB**.
4. The scheduled cron runs hourly to sync the last shipment status.

## Notes

- Domestic Malaysia only.
- No pickup request and no COD.
- Labels are printed from GDEX Prime.
