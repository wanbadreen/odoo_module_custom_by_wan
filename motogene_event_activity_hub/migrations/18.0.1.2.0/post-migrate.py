# -*- coding: utf-8 -*-
"""Seed native Activity Questions for records created with V1.1.

This upgrades only parent Activity question configurations. It does not
recreate existing child Events, registrations, or their saved answers.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for activity in env["motogene.event.activity"].sudo().search([]):
        activity._ensure_activity_questions()
