# MotoGene Promotion Engine — V1 Prototype

## Scope of V1

This first prototype intentionally implements one reusable rule:

**Every X eligible paid box units -> Free Y product**

It is designed to validate the generic engine architecture before adding:

- Minimum Purchase -> Free Product
- Specific Product Purchase -> PWP Eligibility
- Loyalty Point Multiplier (e.g. VIP Double Points)

## Why "Paid Box Units" instead of raw Sale Order quantity?

A Sale Order product can represent a package. Configure each eligible product/package with a factor:

- Normal 1-box product: `1`
- 2-box combo/package: `2`
- 3-box combo/package: `3`
- 8-box combo/package: `8`

Example September–October rule:

- Every X Box Units = `3`
- Reward Product = `KoraGene Sachet`
- Free Quantity Per Reward = `2`
- Repeat Reward = enabled

Then:

- 2 units -> 0 sachets
- 3 units -> 2 sachets
- 6 units -> 4 sachets
- 8 units -> 4 sachets
- 9 units -> 6 sachets

## Test Setup in Odoo

1. Install **MotoGene Promotion Engine** in staging.
2. Open **Promotions -> Promotion Programs**.
3. Create a program:
   - Name: `Sep-Oct 2026 - Every 3 Boxes Free 2 KoraGene`
   - Start: `2026-09-01`
   - End: `2026-10-19`
   - Every X Box Units: `3`
   - Repeat Reward: enabled
   - Reward Product: actual KoraGene sachet SKU
   - Free Quantity Per Reward: `2`
4. Add eligible Sales products/packages and set their **Paid Box Units per Qty**.
5. Activate the program.
6. Create draft quotations and test the matrix below.

## Recommended Test Matrix

| Scenario | Paid Box Units | Expected Free KoraGene |
|---|---:|---:|
| 2 normal boxes | 2 | 0 |
| 3 normal boxes | 3 | 2 |
| 6 normal boxes | 6 | 4 |
| 8-box package x1 | 8 | 4 |
| 8-box package + 1 normal box | 9 | 6 |
| Reduce 9 -> 8 | 8 | 4 |
| Reduce 3 -> 2 | 2 | 0 |
| Recompute repeatedly | unchanged | no duplicate reward line |
| Outside validity date | any | 0 |

## V1 Notes / Boundaries

- Generated reward lines are zero-priced real Sale Order lines, so stock delivery can include the free sachets.
- Generated reward lines are marked and excluded from eligibility calculation.
- Odoo combo child lines are ignored defensively when `combo_item_id` exists, preventing parent + child double counting.
- The program is recalculated when Sale Order lines are created/edited/deleted, when relevant order dates change, when the manual **Recompute Promotions** button is used, and immediately before confirmation.
- Existing quotations do not get recalculated merely because a promotion program itself was edited. Edit the quotation or use **Recompute Promotions**.
- V1 does not yet implement stacking/exclusivity logic between multiple promotions. It keeps one reward line per program, so multiple active programs can coexist, but conflict policy is a V2 decision.
