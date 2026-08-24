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

    name = fields.Char(
        required=True,
    )

    active = fields.Boolean(
        default=True,
    )

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
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )

    date_start = fields.Date(
        required=True,
    )

    date_end = fields.Date(
        required=True,
    )

    # =========================================================
    # CONDITION
    # =========================================================

    rule_type = fields.Selection(
        [
            ("every_x_qty", "Every X Paid Box Units"),
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

    repeat_reward = fields.Boolean(
        string="Repeat Reward",
        default=True,
        help=(
            "If enabled, the reward repeats for every completed threshold. "
            "Example: threshold 3 and 6 eligible units earns the reward twice."
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
        [
            ("free_product", "Free Product"),
        ],
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
                raise ValidationError(
                    _("End date cannot be earlier than start date.")
                )

    # =========================================================
    # BUTTON ACTIONS
    # =========================================================

    def action_activate(self):
        self.write(
            {
                "state": "active",
                "active": True,
            }
        )

    def action_set_draft(self):
        self.write(
            {
                "state": "draft",
                "active": True,
            }
        )

    def action_archive_program(self):
        self.write(
            {
                "state": "archived",
                "active": False,
            }
        )

    # =========================================================
    # PROMOTION VALIDITY
    # =========================================================

    def _is_valid_for_order(self, order):
        self.ensure_one()

        if not self.active:
            return False

        if self.state != "active":
            return False

        if order.company_id != self.company_id:
            return False

        order_date = (
            order.date_order.date()
            if order.date_order
            else fields.Date.context_today(order)
        )

        return self.date_start <= order_date <= self.date_end

    # =========================================================
    # ELIGIBLE PAID BOX UNIT CALCULATION
    # =========================================================

    def _eligible_units_for_order(self, order):
        """
        Calculate total eligible PAID box units.

        There are two eligibility methods:

        1. Explicit Product / Package configuration
           Example:
           - 2-box combo = 2
           - 3-box combo = 3
           - 8-box combo = 8

        2. Eligible Product Tags
           Example:
           - Normal BetAging single box = 1
           - Normal BetVision single box = 1

        Explicit Product / Package configuration has priority
        over Product Tag configuration.
        """

        self.ensure_one()

        # -----------------------------------------------------
        # Explicit product/package mapping
        # -----------------------------------------------------

        factor_by_template = {
            eligibility.product_tmpl_id.id: eligibility.box_units_per_qty
            for eligibility in self.eligibility_line_ids
        }

        # -----------------------------------------------------
        # Eligible tags for normal standalone products
        # -----------------------------------------------------

        eligible_tag_ids = set(self.eligible_product_tag_ids.ids)

        if not factor_by_template and not eligible_tag_ids:
            return 0.0

        total = 0.0

        # -----------------------------------------------------
        # Evaluate Sale Order Lines
        # -----------------------------------------------------

        for line in order.order_line:

            # Ignore sections / notes / empty lines
            if line.display_type or not line.product_id:
                continue

            # -------------------------------------------------
            # Ignore rewards generated by this Promotion Engine
            # -------------------------------------------------

            if line.is_motogene_promo_reward:
                continue

            # -------------------------------------------------
            # Ignore Odoo Combo child lines
            #
            # Parent combo is counted using explicit
            # Paid Box Units configuration.
            # -------------------------------------------------

            if "combo_item_id" in line._fields and line.combo_item_id:
                continue

            # -------------------------------------------------
            # Ignore rewards generated by Odoo Loyalty /
            # Promotion system
            # -------------------------------------------------

            if "reward_id" in line._fields and line.reward_id:
                continue

            if "is_reward_line" in line._fields and line.is_reward_line:
                continue

            template = line.product_id.product_tmpl_id

            # -------------------------------------------------
            # PRIORITY 1:
            # Explicit Product / Package configuration
            # -------------------------------------------------

            factor = factor_by_template.get(template.id)

            # -------------------------------------------------
            # PRIORITY 2:
            # Product Tag configuration
            # -------------------------------------------------

            if not factor and eligible_tag_ids:

                product_tag_ids = set(template.product_tag_ids.ids)

                if product_tag_ids.intersection(eligible_tag_ids):
                    factor = self.tagged_box_units_per_qty

            # Product is not eligible
            if not factor:
                continue

            # -------------------------------------------------
            # Calculate contribution
            #
            # Example:
            # Qty 2 × Paid Box Units 2
            # = 4 eligible paid box units
            # -------------------------------------------------

            total += (
                float(line.product_uom_qty or 0.0)
                * float(factor)
            )

        return total

    # =========================================================
    # REWARD QUANTITY CALCULATION
    # =========================================================

    def _reward_quantity_for_order(self, order):
        self.ensure_one()

        # Promotion not valid for this order
        if not self._is_valid_for_order(order):
            return 0.0

        eligible_units = self._eligible_units_for_order(order)

        # Not enough eligible units
        if eligible_units + 1e-9 < self.threshold_qty:
            return 0.0

        # -----------------------------------------------------
        # Repeat reward
        #
        # Example:
        #
        # Threshold = 3
        # Reward Qty = 2
        #
        # 3 boxes = 2 sachets
        # 6 boxes = 4 sachets
        # 9 boxes = 6 sachets
        # -----------------------------------------------------

        if self.repeat_reward:
            occurrences = math.floor(
                (eligible_units + 1e-9)
                / self.threshold_qty
            )
        else:
            occurrences = 1

        return float(occurrences) * self.reward_qty


# =============================================================
# PROMOTION ELIGIBILITY PRODUCT / PACKAGE
# =============================================================


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