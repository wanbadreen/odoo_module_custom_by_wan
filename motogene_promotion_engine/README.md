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



### Alternative cards (18.0.1.13.0)

Set Card Replacement Policy in the promotion serial-pool setup: Any Available Type, Allowed Types Only, or No Replacement. Restricted mode uses Allowed Replacement Types on each original prefix. Settings remain editable during the campaign and affect future replacement decisions only; setup changes are logged in Notes.

When a quotation has insufficient original serials, choose Proceed With Alternative Cards. Available original cards are allocated first; select replacement types and whole-number quantities for the shortage. Split a row to select multiple prefixes. Alternative allocation supports one quotation at a time. All replacement cards must belong to the same promotion/company. Confirmation rechecks current policy, entitlement and serial stock atomically. A changed shortage or insufficient replacement stock leaves the quotation unconfirmed; close the popup and confirm again to refresh, or use the existing without/available-card options.

Each replacement stores its original prefix, actual printed serial, staff user and allocation time. Reconciliation counts the original entitlement while packing, printing and redemption use the actual card prefix. Later policy changes do not release existing replacement cards. Cancel/physical return/redemption retain their existing state rules. Serial uniqueness and previously reverted serial-range behaviour remain unchanged.

Export/import includes replacement policy and allowed-prefix lists. Older setup JSON files default to Any Available Type.

Tests: Odoo post-install class TestScratchAlternative plus existing promotion, return and redemption suites. For a rollback-only staging allocation check, run tools/staging_check_scratch_alternatives.py in the Odoo.sh Python webshell. This isolated check does not replace UI/DO/print/redemption tests.

### Gift colour / variant choices (18.0.1.14.0)

Each Free Products row defaults to Fixed Variant (existing settings retain their product and behaviour). Customer Chooses Variant instead selects a product template. CS chooses one active, saleable variant per configured gift row in the SO redemption wizard; that variant applies to the row's full configured quantity. Different rows may mix fixed and selectable gifts. The variant must belong to the configured template and be shared or belong to the redemption order company. Choice is not an automatic stock reservation or a promise of stock availability.

The standard redemption form also supports variant choices; Reload Gift Choices refreshes a draft after setup changes. Confirmation revalidates the exact setup rows and variant selection before adding zero-price SO reward lines. The selected variant is used by the generated stock move/DO and recorded in the immutable prize snapshot. Choice records are locked after confirmation/cancellation; cancelling an undelivered/uninvoiced redemption order retains choice history while reversing the reward and card state as before.

Export format 2 includes variant selection mode and template references. Older format 1 files import gifts as Fixed Variant. No existing redemption reward lines or confirmed snapshots are rewritten on upgrade.

Validation: TestScratchGiftVariants covers variant/fixed gifts, missing/wrong/archived selection, changed setup, wizard flow, cancellation history, import compatibility, DO product and company restrictions. Run with Odoo post-install tests in staging.

