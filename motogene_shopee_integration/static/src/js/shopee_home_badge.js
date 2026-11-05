/** @odoo-module **/

import { registry } from "@web/core/registry";

const BADGE_CLASS = "o_motogene_shopee_home_badge";

function attachBadge(count) {
    // Enterprise Odoo 18 home menu app tiles. Never touch other apps.
    const home = document.querySelector(".o_home_menu");
    if (!home) {
        return;
    }
    for (const app of home.querySelectorAll(".o_app")) {
        const label = app.querySelector(".o_caption, .o_app_name, .o_app_caption");
        if (!label || label.textContent.trim() !== "Shopee Integration") {
            continue;
        }
        const existing = app.querySelector("." + BADGE_CLASS);
        if (!count) {
            existing?.remove();
            continue;
        }
        if (!existing) {
            const badge = document.createElement("span");
            badge.className = BADGE_CLASS;
            badge.setAttribute("aria-label", count + " Shopee orders to process");
            app.appendChild(badge);
        }
        const badge = app.querySelector("." + BADGE_CLASS);
        const text = count > 99 ? "99+" : String(count);
        if (badge.textContent !== text) {
            badge.textContent = text;
        }
    }
}

registry.category("services").add("motogene_shopee_home_badge", {
    dependencies: ["orm"],
    start(env, { orm }) {
        let count = 0;
        let busy = false;
        let refreshPending = false;
        const render = () => {
            if (refreshPending) {
                return;
            }
            refreshPending = true;
            requestAnimationFrame(() => {
                refreshPending = false;
                attachBadge(count);
            });
        };
        const refresh = async () => {
            if (busy || document.hidden) {
                return;
            }
            busy = true;
            try {
                count = await orm.call(
                    "motogene.shopee.import.order",
                    "motogene_shopee_pending_order_count",
                    []
                );
                render();
            } catch (error) {
                // UI badge is optional; an API failure must not break navigation.
                console.warn("Shopee badge refresh unavailable", error);
            } finally {
                busy = false;
            }
        };
        const observer = new MutationObserver(render);
        observer.observe(document.body, { childList: true, subtree: true });
        const intervalId = setInterval(refresh, 60000);
        const visibilityHandler = () => {
            if (!document.hidden) {
                refresh();
            }
        };
        document.addEventListener("visibilitychange", visibilityHandler);
        refresh();
        return {
            destroy() {
                clearInterval(intervalId);
                observer.disconnect();
                document.removeEventListener("visibilitychange", visibilityHandler);
            },
        };
    },
});
