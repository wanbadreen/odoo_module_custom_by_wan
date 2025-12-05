# Loyalty Partial Redeem (Odoo 18)

This module enables **partial redemption of loyalty points** directly from a Sales Order.
It integrates with Odoo's official Loyalty Program, Loyalty Cards, and Loyalty History.

The addon allows sales users to open a redemption wizard, select how many points to redeem, and automatically apply a discount line on the quotation while updating the customer's loyalty balance.

---

## ⭐ Key Features

### 1. Redeem Loyalty Points Partially
- User can redeem **any number of points**, not restricted to full balance.
- Points-to-RM conversion uses configurable field `rm\_per\_point`.

### 2. Automatic Discount on Quotation
When points are redeemed:
- A negative-price sale order line is added automatically.
- The discount amount is calculated using:


### 3. Auto-Update Loyalty Card
Upon confirmation:
- Loyalty card balance (`points`) is deducted.
- A Loyalty History record is created to reflect the redemption usage.

### 4. Fully Integrated with Odoo 18 Loyalty
This module works with:
- `loyalty.program`
- `loyalty.card`
- `loyalty.history`
- `sale.order`
- Odoo 18 standard UI

### 5. Smart Error Handling
The wizard shows meaningful messages when:
- No active loyalty program found
- Customer has no loyalty card
- Not enough points to redeem
- Discount product is not found

---

## 📦 Module Workflow

### 1. Open Sales Order
Click the **"Redeem Loyalty Points"** button added inside the header of the quotation view.

### 2. Redemption Wizard Appears
It displays:
- Customer loyalty card
- Available points
- Points to redeem
- RM per point
- Auto-calculated discount amount

### 3. Confirm Redemption
Module automatically:
- Creates a discount line
- Deducts redeemed points
- Logs usage in Loyalty History

---

## 🧩 Technical Components

### Python Models
- `sale\_order.py` (button + wizard launcher)
- `loyalty\_partial\_redeem\_wizard.py` (wizard logic & validations)

### XML Views
- `sale\_order\_view.xml` (button injection)
- `loyalty\_partial\_redeem\_wizard\_view.xml` (wizard form)

### Other Data Files
- `product\_loyalty\_discount.xml` — auto-creates service product for discount lines

---

## 🛠 Requirements

- **Odoo 18**
- Modules:
- `sale\_management`
- `loyalty`

---

## 📁 Installation

1. Download or clone this repository into your Odoo addons folder.
2. Update Odoo Apps list.
3. Install the module **Morimoto Loyalty Partial Redeem**.
4. Ensure a Loyalty Program is active and Loyalty Cards are assigned to customers.

---

## 📘 Changelog

### 18.0.1.0.0
- Initial release
- Add loyalty partial redemption workflow
- Discount product auto-detection
- Loyalty history logging

---

## 👤 Author
Developed by **Wan Badreen**

## 📝 How to apply this module in your database

1. **Copy to addons path:** Place the `odoo_loyalty_partial_redeem` folder in your Odoo 18 addons directory or add the repository path to `addons_path`.
2. **Update the apps list:** From the Odoo Apps menu click **Update Apps List** (or run `-u odoo_loyalty_partial_redeem` from the command line).
3. **Install the module:** Search for **"Morimoto Loyalty Partial Redeem"** and install it.
4. **Ensure a loyalty program exists:** Create/activate a loyalty program of type **Loyalty**, then issue loyalty cards to customers so they have a starting balance.
5. **Use in a quotation:** Open any quotation for a customer with points and click **Redeem Loyalty Points**. Enter the points to redeem; the wizard will add a negative service line using the bundled **Loyalty Point Redemption** product.
6. **Confirm the order:** Confirming the order reduces the card balance and logs a loyalty history entry showing the redeemed points.

## 📞 Support
For support or custom enhancements, feel free to contact **wanbadreen@gmail.com** or open an issue on the [project website](https://github.com/wanbadreen/odoo_loyalty_partial_redeem).
