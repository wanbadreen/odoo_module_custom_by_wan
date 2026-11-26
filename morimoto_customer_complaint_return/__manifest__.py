# -*- coding: utf-8 -*-
{
    "name": "Customer Complaint & Return Management",
    "summary": "Centralised customer complaints and product returns linked to sales & stock",
    "version": "20.0.1.0.0",
    "author": "WanBadreen",
    "website": "https://www.morimoto.com",
    "category": "Customer Relationship Management",
    "license": "LGPL-3",
    "depends": ["base", "mail", "sale", "account", "stock"],
    "data": [
        "security/ir.access.csv",
        "data/complaint_sequence.xml",
        "views/customer_complaint_actions.xml",
        "views/customer_complaint_views.xml",
        "views/customer_complaint_menus.xml",
    ],
    "installable": True,
    "application": True,
    "icon": "morimoto_customer_complaint_return/static/description/icon.png",
}
