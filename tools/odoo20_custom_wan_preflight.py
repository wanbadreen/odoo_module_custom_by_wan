#!/usr/bin/env python3
"""Read-only syntax/manifest/security preflight for the Custom WAN Odoo 20 branch.

This is NOT an Odoo registry, database upgrade, integration or UAT test.
Run from any directory: python3 tools/odoo20_custom_wan_preflight.py
"""
import ast
import csv
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "loyalty_coupon_validity_extension",
    "morimoto_customer_complaint_return",
    "morimoto_mobile",
    "morimoto_mobile_integration_tests",
    "motogene_event_activity_hub",
    "motogene_event_contacts",
    "motogene_event_daily_summary",
    "motogene_event_operations_bundle",
    "motogene_event_self_service",
    "motogene_gdex_normal",
    "motogene_promotion_engine",
    "motogene_shopee_integration",
)
errors = []
warnings = []
counts = {"python": 0, "xml": 0, "acl_rows": 0}


def failure(path, message):
    errors.append(f"{path}: {message}")


for module in MODULES:
    base = ROOT / module
    mpath = base / "__manifest__.py"
    if not mpath.is_file():
        failure(module, "Missing manifest")
        continue
    try:
        manifest = ast.literal_eval(mpath.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError) as exc:
        failure(module, f"Invalid manifest: {exc}")
        continue
    if not str(manifest.get("version", "")).startswith("20.0"):
        failure(module, "Version is not 20.0")
    files = list(manifest.get("data", []))
    for bundle in manifest.get("assets", {}).values():
        files.extend(bundle if isinstance(bundle, list) else [bundle])
    for relative in files:
        if not (base / relative).exists() and not (ROOT / relative).exists():
            failure(module, f"Manifest missing file: {relative}")
    for entry in manifest.get("data", []):
        if "ir.model.access.csv" in entry:
            failure(module, "Legacy ir.model.access.csv referenced in manifest")
    for path in base.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
            counts["python"] += 1
            if "_sql_constraints" in source and "models" in path.parts:
                failure(path.relative_to(ROOT), "Legacy _sql_constraints found")
            legacy_patterns = {
                "from odoo.tools.misc import xlsxwriter":
                    "Odoo 20 no longer exports xlsxwriter from odoo.tools.misc",
                "from odoo.osv import expression":
                    "Deprecated osv.expression import",
                "request.env[\"website.visitor\"]._get_visitor_from_request":
                    "Odoo 20 visitor helper is on ir.http",
            }
            for pattern, description in legacy_patterns.items():
                if pattern in source:
                    failure(path.relative_to(ROOT), description)
            if module == "motogene_event_activity_hub" and path.name == "question.py":
                if "unique(event_id, motogene_activity_source_id)" in source:
                    failure(path.relative_to(ROOT), "Event question.event_id removed in Odoo 20")

        except (SyntaxError, UnicodeDecodeError) as exc:
            failure(path.relative_to(ROOT), f"Python parse failed: {exc}")
    for path in base.rglob("*.xml"):
        try:
            tree = ET.parse(path)
            counts["xml"] += 1
            for node in tree.iter("record"):
                if node.attrib.get("model") == "ir.rule":
                    failure(path.relative_to(ROOT), "Legacy ir.rule record remains")
                if node.attrib.get("model") == "ir.actions.server":
                    if any(field.attrib.get("name") == "groups_id" for field in node.iter("field")):
                        failure(path.relative_to(ROOT), "Server actions use group_ids in Odoo 20")

        except ET.ParseError as exc:
            failure(path.relative_to(ROOT), f"XML parse failed: {exc}")
    access = base / "security" / "ir.access.csv"
    if access.is_file():
        with access.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            expected = ["id", "name", "model_id", "group_id/id", "operation", "domain"]
            if reader.fieldnames != expected:
                failure(access.relative_to(ROOT), f"Wrong header: {reader.fieldnames}")
            else:
                for index, row in enumerate(reader, start=2):
                    op = row["operation"] or ""
                    if not op or len(set(op)) != len(op) or not set(op) <= set("crud"):
                        failure(access.relative_to(ROOT), f"Invalid operation at line {index}: {op!r}")
                    if (row["model_id"] or "").startswith("model_"):
                        failure(access.relative_to(ROOT), f"Legacy model XML ID at line {index}: {row['model_id']}")
                    if not row["id"] or not row["model_id"]:
                        failure(access.relative_to(ROOT), f"Missing id/model at line {index}")
                    counts["acl_rows"] += 1
    elif any("ir.access.csv" in f for f in files):
        failure(module, "Manifest references absent ir.access.csv")

print(f"Custom WAN static preflight: {len(MODULES)} addons, "
      f"{counts['python']} Python files, {counts['xml']} XML files, "
      f"{counts['acl_rows']} ACL rows")
if warnings:
    print("WARNINGS:")
    for warning in warnings:
        print(" -", warning)
if errors:
    print("ERRORS:")
    for error in errors:
        print(" -", error)
    sys.exit(1)
print("PASS: Syntax, referenced files and basic Odoo 20 ACL structure only.")
print("NOT TESTED: registry/install, website XPaths, access semantics, API calls, Studio or database upgrade.")
