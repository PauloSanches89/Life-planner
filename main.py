"""Open the Life planner window."""

import webview

from planner.alarms import AlarmScheduler
from planner.bridge import JsApi
from planner.clock import SystemClock
from planner.service import PlannerService
from planner.store import Store, default_store_root


def main() -> None:
    clock = SystemClock()
    service = PlannerService(Store(default_store_root(), clock), clock)
    scheduler = AlarmScheduler(service, clock)
    # A relative path avoids the "#" in this repo path, which the loader would treat as a fragment.
    page = "ui/index.html"
    scheduler.start()
    try:
        webview.create_window(
            "Life planner",
            page,
            js_api=JsApi(service, clock),
            width=1100,
            height=720,
            min_size=(800, 560),
        )
        webview.start()
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
