/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";

const wizardAction = "motogene_event_activity_hub.action_event_creation_wizard";

// Only event.event New in kanban/list changes; all other models retain Odoo.
patch(KanbanController.prototype, {
    async createRecord() {
        if (this.props.resModel === "event.event") {
            return this.actionService.doAction(wizardAction);
        }
        return super.createRecord(...arguments);
    },
});
patch(ListController.prototype, {
    async createRecord() {
        if (this.props.resModel === "event.event") {
            return this.actionService.doAction(wizardAction);
        }
        return super.createRecord(...arguments);
    },
});
