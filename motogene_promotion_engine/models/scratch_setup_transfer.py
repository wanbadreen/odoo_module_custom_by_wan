# -*- coding: utf-8 -*-
import base64
import json
import math
from odoo import Command, fields, models, _
from odoo.exceptions import UserError


class ScratchSetupTransferProgram(models.Model):
    _inherit = "motogene.promotion.program"

    def _scratch_setup_reference(self, product):
        return {
            "xmlid": product.get_external_id().get(product.id) or None,
            "code": product.default_code or None, "name": product.name,
        }

    def _scratch_setup_payload(self):
        self.ensure_one()
        if self.reward_type != "scratch_cards":
            raise UserError(_("Select a Scratch & Win promotion."))
        return {
            "format": "motogene-scratch-setup", "version": 2,
            "source_name": self.name,
            "currency": (self.company_id or self.env.company).currency_id.name,
            "settings": {
                "date_start": fields.Date.to_string(self.date_start),
                "date_end": fields.Date.to_string(self.date_end),
                "scratch_redemption_expiry_date": fields.Date.to_string(self.scratch_redemption_expiry_date),
                "minimum_amount": self.minimum_amount,
                "scratch_vip_prefix": self.scratch_vip_prefix,
                "scratch_extra_prefix": self.scratch_extra_prefix,
                "scratch_replacement_policy": self.scratch_replacement_policy,
            },
            "packages": [{
                "product": self._scratch_setup_reference(p.product_tmpl_id),
                "advertised_cards": p.advertised_cards, "card_prefixes": p.card_prefixes or "",
            } for p in self.scratch_package_line_ids],
            "pools": [{
                "prefix": p.prefix, "first_number": p.first_number, "last_number": p.last_number,
                "prize_description": p.prize_description or "",
                "allowed_replacement_prefixes": p.allowed_replacement_pool_ids.mapped("prefix"),
                "redemption_reward_type": p.redemption_reward_type or False,
                "rebate_amount": p.rebate_amount,
                "rebate_product": self._scratch_setup_reference(p.rebate_product_id) if p.rebate_product_id else None,
                "mystery_quantity": p.mystery_quantity,
                "products": [{
                    "variant_mode": line.variant_mode,
                    "product": self._scratch_setup_reference(line.product_id) if line.variant_mode == "fixed" else None,
                    "template": self._scratch_setup_reference(line.product_tmpl_id) if line.variant_mode == "choose" else None,
                    "quantity": line.quantity,
                } for line in p.reward_product_line_ids],
            } for p in self.scratch_serial_pool_ids],
        }

    def action_export_scratch_setup(self):
        self.ensure_one()
        self.check_access_rights("read")
        self.check_access_rule("read")
        wizard = self.env["motogene.scratch.setup.transfer"].create({
            "program_id": self.id, "mode": "export", "filename": "scratch-setup-%s.json" % self.id,
            "file_data": base64.b64encode(json.dumps(self._scratch_setup_payload(), ensure_ascii=False, indent=2).encode()),
        })
        return wizard._window_action()

    def action_import_scratch_setup(self):
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        wizard = self.env["motogene.scratch.setup.transfer"].create({"program_id": self.id, "mode": "import"})
        return wizard._window_action()


class ScratchSetupTransfer(models.TransientModel):
    _name = "motogene.scratch.setup.transfer"
    _description = "Transfer Scratch Promotion Setup"

    program_id = fields.Many2one("motogene.promotion.program", required=True, readonly=True)
    mode = fields.Selection([("export", "Export"), ("import", "Import")], required=True, readonly=True)
    filename = fields.Char()
    file_data = fields.Binary(string="Setup File")

    def _window_action(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window", "name": _("Transfer Scratch Setup"),
            "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "new",
            "view_id": self.env.ref("motogene_promotion_engine.view_scratch_setup_transfer").id,
        }

    def _resolve_product(self, reference, model):
        if not isinstance(reference, dict) or not reference.get("name"):
            raise UserError(_("Invalid product reference in setup file."))
        company = self.program_id.company_id or self.env.company
        Product = self.env[model]
        domain = [("sale_ok", "=", True), ("company_id", "in", [False, company.id])]
        xmlid = reference.get("xmlid")
        product = self.env.ref(xmlid, raise_if_not_found=False) if xmlid else False
        if product and product._name == model and product.active and product.sale_ok and (not product.company_id or product.company_id == company):
            return product
        # Codes are stable across databases. Exact names are a fallback only when unique.
        for key, field in (("code", "default_code"), ("name", "name")):
            if not reference.get(key):
                continue
            products = Product.search(domain + [(field, "=", reference[key])], limit=2)
            if len(products) > 1:
                raise UserError(_("Product %s has multiple matches. Use a unique Internal Reference in both databases.") % reference["name"])
            if products:
                return products
        raise UserError(_("Product %s was not found. Create it in the destination database with the same Internal Reference, then import again.") % reference["name"])

    def _resolve_gift_setup(self, entry):
        mode = entry.get("variant_mode", "fixed")
        if mode not in ("fixed", "choose"):
            raise UserError(_("Unknown gift variant selection mode."))
        vals = {"variant_mode": mode, "quantity": entry["quantity"]}
        if mode == "choose":
            vals["product_tmpl_id"] = self._resolve_product(entry["template"], "product.template").id
            vals["product_id"] = False
        else:
            vals["product_id"] = self._resolve_product(entry["product"], "product.product").id
            vals["product_tmpl_id"] = False
        return vals

    def action_import(self):
        self.ensure_one()
        if self.mode != "import":
            raise UserError(_("Open Import Setup to import a file."))
        program = self.program_id
        program.check_access_rights("write")
        program.check_access_rule("write")
        if program.reward_type != "scratch_cards" or program.state != "draft":
            raise UserError(_("Import into a draft Scratch & Win promotion."))
        # Lock the destination against activation/allocation while replacing setup.
        self.env.cr.execute("SELECT id FROM motogene_promotion_program WHERE id = %s FOR UPDATE", [program.id])
        program.invalidate_recordset()
        if program.state != "draft" or self.env["motogene.scratch.picking.card"].search_count([("program_id", "=", program.id)]):
            raise UserError(_("Import is only allowed before this promotion has allocated any cards. Existing history is protected."))
        if not self.file_data:
            raise UserError(_("Upload the exported setup JSON file."))
        pools = program.scratch_serial_pool_ids
        if pools:
            pools.flush_recordset()
            self.env.cr.execute("SELECT id FROM motogene_scratch_serial_pool WHERE id IN %s ORDER BY id FOR UPDATE", [tuple(sorted(pools.ids))])
            pools.invalidate_recordset()
        if any(p.next_number != p.first_number for p in pools) or self.env["motogene.scratch.picking.card"].search_count([("program_id", "=", program.id)]):
            raise UserError(_("Import cannot modify a promotion whose serial pools have been used."))
        try:
            raw = base64.b64decode(self.file_data, validate=True)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("file too large")
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("format") != "motogene-scratch-setup" or data.get("version") not in (1, 2):
                raise ValueError("unsupported format")
            settings = data["settings"]
            if not isinstance(settings, dict):
                raise ValueError("invalid settings")
            settings.setdefault("scratch_replacement_policy", "any")
            if settings["scratch_replacement_policy"] not in ("any", "allowed", "none"):
                raise ValueError("invalid replacement policy")
            setting_keys = {"date_start", "date_end", "scratch_redemption_expiry_date", "minimum_amount", "scratch_vip_prefix", "scratch_extra_prefix", "scratch_replacement_policy"}
            if not isinstance(settings, dict) or set(settings) != setting_keys:
                raise ValueError("invalid settings")
            if not isinstance(data["packages"], list) or not isinstance(data["pools"], list):
                raise ValueError("invalid lists")
            if not math.isfinite(float(settings["minimum_amount"])) or float(settings["minimum_amount"]) <= 0:
                raise ValueError("invalid purchase amount")
            for date in ("date_start", "date_end", "scratch_redemption_expiry_date"):
                if not settings[date] or not fields.Date.to_date(settings[date]):
                    raise ValueError("missing date")
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise UserError(_("Invalid or incomplete Scratch Setup JSON file: %s") % str(exc)) from exc
        if data.get("currency") != (program.company_id or self.env.company).currency_id.name:
            raise UserError(_("Setup currency does not match the destination promotion company."))
        # Resolve all products before making changes; the savepoint also guarantees no partial import.
        try:
            package_vals = []
            for package in data["packages"]:
                product = self._resolve_product(package["product"], "product.template")
                package_vals.append({"product_tmpl_id": product.id, "advertised_cards": package["advertised_cards"], "card_prefixes": package["card_prefixes"]})
            pool_vals = []
            for pool in data["pools"]:
                vals = {k: pool[k] for k in ("prefix", "first_number", "last_number", "prize_description", "redemption_reward_type", "rebate_amount", "mystery_quantity")}
                if vals["redemption_reward_type"] not in ("products", "rebate", "mystery"):
                    raise ValueError("configure reward type for each prefix before export")
                if not all(math.isfinite(float(vals[k])) for k in ("first_number", "last_number", "rebate_amount", "mystery_quantity")):
                    raise ValueError("non-finite number")
                vals["rebate_product_id"] = self._resolve_product(pool["rebate_product"], "product.product").id if pool.get("rebate_product") else False
                vals["reward_product_line_ids"] = [Command.clear()] + [Command.create({
                    **self._resolve_gift_setup(p),
                }) for p in pool["products"]]
                if any(not math.isfinite(float(p["quantity"])) for p in pool["products"]):
                    raise ValueError("non-finite gift quantity")
                pool_vals.append(vals)
            if len({p["prefix"] for p in pool_vals}) != len(pool_vals):
                raise ValueError("duplicate prefixes")
            if set(program.scratch_serial_pool_ids.mapped("prefix")) - {p["prefix"] for p in pool_vals}:
                raise ValueError("destination contains prefixes absent from file; use a new draft promotion")
            prefixes = {p["prefix"] for p in pool_vals}
            needed = {settings["scratch_vip_prefix"], settings["scratch_extra_prefix"]}
            for p in package_vals:
                needed.update(t.strip().upper() for t in p["card_prefixes"].split(",") if t.strip())
            if not needed.issubset(prefixes):
                raise ValueError("missing serial pools for package, VIP or extra-spend prefixes")
            replacement_map = {p["prefix"]: p.get("allowed_replacement_prefixes", []) for p in data["pools"]}
            for prefix, targets in replacement_map.items():
                if not isinstance(targets, list) or any(not isinstance(t, str) or t not in prefixes or t == prefix for t in targets):
                    raise ValueError("invalid allowed replacement prefixes")
            with self.env.cr.savepoint():
                program.write(dict(settings, scratch_package_line_ids=[Command.clear()] + [Command.create(p) for p in package_vals]))
                for vals in pool_vals:
                    pool = program.scratch_serial_pool_ids.filtered(lambda p: p.prefix == vals["prefix"])
                    if pool:
                        if (pool.first_number, pool.last_number) != (vals["first_number"], vals["last_number"]):
                            if pool.next_number != pool.first_number:
                                raise UserError(_("Serial range %s has advanced; its counter cannot be reset by import.") % pool.prefix)
                            vals["next_number"] = vals["first_number"]
                        pool.write(vals)
                    else:
                        pool = self.env["motogene.scratch.serial.pool"].create(dict(vals, program_id=program.id))
                    pool._validate_redemption_reward()
                for pool in program.scratch_serial_pool_ids:
                    pool.write({"allowed_replacement_pool_ids": [Command.set(
                        program.scratch_serial_pool_ids.filtered(lambda p: p.prefix in replacement_map[pool.prefix]).ids
                    )]})
                if not program.scratch_package_line_ids or not program.scratch_serial_pool_ids:
                    raise UserError(_("Setup needs eligible packages and serial pools."))
        except (ValueError, TypeError, KeyError) as exc:
            raise UserError(_("Invalid setup content: %s") % str(exc)) from exc
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("Setup imported"), "message": _("Packages, serial ranges, prizes and expiry imported. Promotion remains Draft; review before activating."),
            "type": "success", "sticky": True, "next": {"type": "ir.actions.act_window_close"},
        }}


