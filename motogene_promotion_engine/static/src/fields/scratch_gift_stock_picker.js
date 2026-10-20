/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

export class ScratchGiftStockPicker extends Component {
    static template = "motogene_promotion_engine.ScratchGiftStockPicker";
    static props = { ...standardFieldProps };

    setup() {
        this.state = useState({ busy: false, error: "" });
    }

    get options() {
        return this.props.record.data[this.props.name] || { groups: [] };
    }

    selectedId(group) {
        if (this.options.groups.length === 1) {
            return this.props.record.data.selected_variant_id?.[0] || false;
        }
        return this.choiceRecord(group)?.data.product_id?.[0] || false;
    }

    choiceRecord(group) {
        return this.props.record.data.gift_choice_ids?.records.find(
            (record) => record.data.reward_line_id?.[0] === group.reward_line_id
        );
    }

    async selectVariant(group, variant) {
        if (this.state.busy || !variant.available_to_select) {
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const value = [variant.id, variant.name];
            if (this.options.groups.length === 1) {
                // Triggers the existing onchange/inverse, which updates the
                // actual gift choice copied to the redemption record.
                await this.props.record.update({ selected_variant_id: value });
            } else {
                const choice = this.choiceRecord(group);
                if (!choice) {
                    this.state.error = "Gift setup changed. Reopen redemption to load the current choices.";
                    return;
                }
                await choice.update({ product_id: value });
            }
        } finally {
            this.state.busy = false;
        }
    }
}

registry.category("fields").add("scratch_gift_stock_picker", {
    component: ScratchGiftStockPicker,
    supportedTypes: ["json"],
});
