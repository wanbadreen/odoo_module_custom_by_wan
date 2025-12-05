# Loyalty Partial Redeem

Sell orders can apply a partial redemption of loyalty points with a wizard that proposes the customer's active loyalty card and records the usage in the history.

## Features

- Compatible with **Odoo 18.0** and modules `sale_management` and `loyalty`.
- Adds a "Redeem Loyalty Points" action on sale orders to open a guided wizard.
- Lets the user choose how many points to consume and converts them into a discount line.
- Updates the loyalty card balance and logs the redemption in loyalty history.
- Ships with a default **Loyalty Point Redemption** product to use for the discount line.

## Installation

1. Install the required apps: *Sales* and *Loyalty*.
2. Install this module. The data file creates a non-sellable service product named **Loyalty Point Redemption**.
3. Ensure your company currency and loyalty program are configured.

## Usage

1. Create a quotation for a customer that has an active loyalty card with available points.
2. Click **Redeem Loyalty Points** on the sale order.
3. Enter the number of points to use and confirm; the wizard will add a negative line using the redemption product and deduct points from the loyalty card.

## Support & Contact

For support or inquiries, visit the project page: <https://github.com/wanbadreen/odoo_loyalty_partial_redeem>.
