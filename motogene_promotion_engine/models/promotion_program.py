# -*- coding: utf-8 -*-

import math

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class MotogenePromotionProgram(models.Model):
    _name = "motogene.promotion.program"
    _description = "MotoGene Promotion Program"
    _order = "priority, id"

    # =========================================================
    # BASIC INFORMATION
    # =========================================================

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("archived", "Archived"),
        ],
        default="draft",
        required=True,
    )

    priority = fields.Integer(
        default=10,
        help="Lower number is evaluated first.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=False,
        default=lambda self: self.env.company,
        index=True,
        help="Leave blank to apply this promotion to all companies.",
    )

    currency_id = fields.Many2one(
        "res.currency",
        compute="_compute_currency_id",
        readonly=True,
    )

    date_start = fields.Date(required=True)
    date_end = fields.Date(required=True)

    # =========================================================
    # CONDITION
    # =========================================================

    rule_type = fields.Selection(
        [
            ("every_x_qty", "Every X Paid Box Units"),
            ("minimum_purchase", "Minimum Purchase Amount"),
        ],
        string="Condition Type",
        required=True,
        default="every_x_qty",
    )

    threshold_qty = fields.Float(
        string="Every X Box Units",
        required=True,
        default=3.0,
        help="Example: 3 means every 3 eligible paid box units.",
    )

    minimum_amount = fields.Monetary(
        string="Minimum Purchase Amount",
        currency_field="currency_id",
        help="Minimum untaxed purchase amount required to earn the reward.",
    )

    amount_basis = fields.Selection(
        [
            ("before_discount", "Before Discount"),
            ("after_discount", "After Product Discount"),
        ],
        string="Amount Basis",
        required=True,
        default="after_discount",
        help=(
            "Before Discount uses quantity x unit price. "
            "After Product Discount uses the untaxed line subtotal after normal line discounts."
        ),
    )

    loyalty_redemption_handling = fields.Selection(
        [
            ("ignore", "Ignore Redemption"),
            ("deduct", "Deduct Redemption"),
        ],
        string="Loyalty Redemption",
        required=True,
        default="ignore",
        help=(
            "Ignore Redemption keeps loyalty point redemption out of the eligibility calculation. "
            "Deduct Redemption treats the negative redemption line as reducing eligible spend."
        ),
    )

    shipping_handling = fields.Selection(
        [
            ("exclude", "Exclude Shipping"),
            ("include", "Include Shipping"),
        ],
        string="Shipping",
        required=True,
        default="exclude",
        help="Choose whether delivery/shipping charges contribute to the minimum purchase amount.",
    )

    repeat_reward = fields.Boolean(
        string="Repeat Reward",
        default=True,
        help=(
            "If enabled, the reward repeats for every completed threshold. "
            "For box rules, 6 units with threshold 3 earns twice. "
            "For amount rules, RM4,000 with threshold RM2,000 earns twice."
        ),
    )

    # =========================================================
    # ELIGIBILITY - PRODUCT TAG
    # =========================================================

    eligible_product_tag_ids = fields.Many2many(
        "product.tag",
        "motogene_promo_program_product_tag_rel",
        "program_id",
        "tag_id",
        string="Eligible Product Tags",
        help=(
            "Normal standalone products carrying any of these tags are eligible. "
            "Use this for single-box products. "
            "Explicit Product / Package configuration takes priority."
        ),
    )

    tagged_box_units_per_qty = fields.Float(
        string="Tagged Product Paid Box Units",
        default=1.0,
        required=True,
        help=(
            "Number of paid box units contributed by each ordered quantity "
            "of a product matching an Eligible Product Tag. "
            "Normally keep this at 1 for single-box products."
        ),
    )

    # =========================================================
    # ELIGIBILITY - EXPLICIT PRODUCT / PACKAGE
    # =========================================================

    eligibility_line_ids = fields.One2many(
        "motogene.promotion.eligibility",
        "program_id",
        string="Eligible Products / Packages",
    )

    # =========================================================
    # REWARD
    # =========================================================

    reward_type = fields.Selection(
        [("free_product", "Free Product")],
        required=True,
        default="free_product",
    )

    reward_product_id = fields.Many2one(
        "product.product",
        string="Free Product",
        required=True,
        domain=[("sale_ok", "=", True)],
    )

    reward_qty = fields.Float(
        string="Free Quantity Per Reward",
        required=True,
        default=2.0,
    )

    reward_line_label = fields.Char(
        default="Promotion Reward",
        help="Label used on the generated zero-price Sales Order line.",
    )

    notes = fields.Text()

    # =========================================================
    # COMPUTED FIELDS
    # =========================================================

    @api.depends("company_id")
    def _compute_currency_id(self):
        for program in self:
            program.currency_id = (
                program.company_id.currency_id
                if program.company_id
                else self.env.company.currency_id
            )

    # =========================================================
    # SQL CONSTRAINTS
    # =========================================================

    _sql_constraints = [
        (
            "threshold_positive",
            "CHECK(threshold_qty > 0)",
            "Every X quantity must be greater than zero.",
        ),
        (
            "reward_qty_positive",
            "CHECK(reward_qty > 0)",
            "Reward quantity must be greater than zero.",
        ),
        (
            "tagged_box_units_positive",
            "CHECK(tagged_box_units_per_qty > 0)",
            "Tagged product paid box units must be greater than zero.",
        ),
    ]

    # =========================================================
    # VALIDATION
    # =========================================================

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for program in self:
            if (
                program.date_start
                and program.date_end
                and program.date_end < program.date_start
            ):
                raise ValidationError(_("End date cannot be earlier than start date."))

    @api.constrains("rule_type", "minimum_amount")
    def _check_minimum_amount(self):
        for program in self:
            if program.rule_type == "minimum_purchase" and program.minimum_amount <= 0:
                raise ValidationError(_("Minimum Purchase Amount must be greater than zero."))

    # =========================================================
    # BUTTON ACTIONS
    # =========================================================

    def action_activate(self):
        self.write({"state": "active", "active": True})

    def action_set_draft(self):
        self.write({"state": "draft", "active": True})

    def action_archive_program(self):
        self.write({"state": "archived", "active": False})

    # =========================================================
    # PROMOTION VALIDITY
    # =========================================================

    def _is_valid_for_order(self, order):
        self.ensure_one()

        if not self.active or self.state != "active":
            return False

        # A configured company restricts the promotion to that company.
        # Blank company means the promotion is valid for all companies.
        if self.company_id and order.company_id != self.company_id:
            return False

        order_date = (
            order.date_order.date()
            if order.date_order
            else fields.Date.context_today(order)
        )

        return self.date_start <= order_date <= self.date_end

    # =========================================================
    # COMMON LINE HELPERS
    # =========================================================

    @staticmethod
    def _is_loyalty_redemption_line(line):
        """Detect the custom Loyalty Partial Redeem negative order line safely."""
        if "is_loyalty_redeem_line" in line._fields and line.is_loyalty_redeem_line:
            return True

        product = line.product_id
        if not product:
            return False

        default_code = (product.default_code or "").strip().lower()
        product_name = (product.name or "").strip().lower()

        return bool(
            default_code in {
                "loyalty point redemption",
                "loyalty points redemption",
            }
            or product_name in {
                "loyalty point redemption",
                "loyalty points redemption",
                "loyalty redemption discount",
            }
        )

    @staticmethod
    def _is_shipping_line(line):
        return bool("is_delivery" in line._fields and line.is_delivery)

    @staticmethod
    def _is_other_reward_line(line):
        if "reward_id" in line._fields and line.reward_id:
            return True
        if "is_reward_line" in line._fields and line.is_reward_line:
            return True
        return False

    # =========================================================
    # V1 - ELIGIBLE PAID BOX UNIT CALCULATION
    # =========================================================

    def _eligible_units_for_order(self, order):
        """Calculate eligible paid box units for the Every X Paid Box Units rule."""
        self.ensure_one()

        factor_by_template = {
            eligibility.product_tmpl_id.id: eligibility.box_units_per_qty
            for eligibility in self.eligibility_line_ids
        }
        eligible_tag_ids = set(self.eligible_product_tag_ids.ids)

        if not factor_by_template and not eligible_tag_ids:
            return 0.0

        total = 0.0

        for line in order.order_line:
            if line.display_type or not line.product_id:
                continue

            # Never let generated rewards earn more rewards.
            if line.is_motogene_promo_reward:
                continue

            # Parent combo/package is counted using its explicit configured factor.
            # Child combo lines must not be counted again for the box-unit rule.
            if "combo_item_id" in line._fields and line.combo_item_id:
                continue

            # Exclude rewards generated by other Odoo promotion/loyalty mechanisms.
            if self._is_other_reward_line(line):
                continue

            template = line.product_id.product_tmpl_id

            # Priority 1: explicit Product / Package configuration.
            factor = factor_by_template.get(template.id)

            # Priority 2: tagged normal single product.
            if not factor and eligible_tag_ids:
                product_tag_ids = set(template.product_tag_ids.ids)
                if product_tag_ids.intersection(eligible_tag_ids):
                    factor = self.tagged_box_units_per_qty

            if not factor:
                continue

            total += float(line.product_uom_qty or 0.0) * float(factor)

        return total

    # =========================================================
    # V1.2 - MINIMUM PURCHASE AMOUNT CALCULATION
    # =========================================================

    def _eligible_purchase_amount_for_order(self, order):
        """
        Calculate untaxed eligible spend for Minimum Purchase Amount rules.

        Rules:
        - Promotion Engine reward lines are always excluded.
        - Other Odoo reward lines are excluded.
        - Loyalty redemption can be ignored or deducted.
        - Shipping can be included or excluded.
        - Before Discount uses qty x unit price.
        - After Product Discount uses price_subtotal.

        Combo child lines are intentionally NOT excluded here because Odoo can
        allocate the paid combo price across the child sale lines. Summing the
        monetary sale lines therefore follows the actual order amount instead
        of the V1 paid-box-unit representation.
        """
        self.ensure_one()

        total = 0.0

        for line in order.order_line:
            if line.display_type or not line.product_id:
                continue

            if line.is_motogene_promo_reward:
                continue

            is_loyalty_redemption = self._is_loyalty_redemption_line(line)

            if is_loyalty_redemption:
                if self.loyalty_redemption_handling == "ignore":
                    continue
            else:
                # Other reward/free lines should never contribute to purchase amount.
                if self._is_other_reward_line(line):
                    continue

            if (
                self.shipping_handling == "exclude"
                and self._is_shipping_line(line)
            ):
                continue

            if self.amount_basis == "before_discount":
                line_amount = float(line.product_uom_qty or 0.0) * float(line.price_unit or 0.0)
            else:
                line_amount = float(line.price_subtotal or 0.0)

            total += line_amount

        # Respect the order currency precision before threshold comparison.
        if order.currency_id:
            total = order.currency_id.round(total)

        return total

    # =========================================================
    # REWARD QUANTITY CALCULATION
    # =========================================================

    def _reward_quantity_for_order(self, order):
        self.ensure_one()

        if not self._is_valid_for_order(order):
            return 0.0

        # V1.2: Minimum Purchase Amount -> Free Product
        if self.rule_type == "minimum_purchase":
            eligible_amount = self._eligible_purchase_amount_for_order(order)
            minimum_amount = float(self.minimum_amount or 0.0)

            if minimum_amount <= 0 or eligible_amount + 1e-9 < minimum_amount:
                return 0.0

            occurrences = (
                math.floor((eligible_amount + 1e-9) / minimum_amount)
                if self.repeat_reward
                else 1
            )
            return float(occurrences) * self.reward_qty

        # V1: Every X Paid Box Units -> Free Product
        if self.rule_type == "every_x_qty":
            eligible_units = self._eligible_units_for_order(order)

            if eligible_units + 1e-9 < self.threshold_qty:
                return 0.0

            occurrences = (
                math.floor((eligible_units + 1e-9) / self.threshold_qty)
                if self.repeat_reward
                else 1
            )
            return float(occurrences) * self.reward_qty

        return 0.0


class MotogenePromotionEligibility(models.Model):
    _name = "motogene.promotion.eligibility"
    _description = "MotoGene Promotion Eligible Product"
    _order = "id"

    program_id = fields.Many2one(
        "motogene.promotion.program",
        required=True,
        ondelete="cascade",
        index=True,
    )

    product_tmpl_id = fields.Many2one(
        "product.template",
        string="Eligible Product / Package",
        required=True,
        domain=[("sale_ok", "=", True)],
    )

    box_units_per_qty = fields.Float(
        string="Paid Box Units per Qty",
        required=True,
        default=1.0,
        help=(
            "How many paid boxes one ordered quantity represents. "
            "Normal box = 1; "
            "2-box combo/package = 2; "
            "3-box combo/package = 3; "
            "8-box package = 8."
        ),
    )

    _sql_constraints = [
        (
            "box_units_positive",
            "CHECK(box_units_per_qty > 0)",
            "Paid box units must be greater than zero.",
        ),
        (
            "program_product_unique",
            "UNIQUE(program_id, product_tmpl_id)",
            "This product is already configured for the promotion.",
        ),
    ]
