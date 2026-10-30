"""Open the Life planner in a local window and serve the same page over HTTP."""

from pathlib import Path

import webview

from planner.alarms import AlarmScheduler
from planner.assistant import OllamaWarmup
from planner.clock import SystemClock
from planner.crypto import load_master_key
from planner.server import PlannerApp
from planner.settings import Settings, load_dotenv
from planner.store import default_store_root


def main() -> None:
    root = default_store_root()
    load_dotenv(Path(__file__).resolve().parent / ".env")
    settings = Settings.from_env()
    clock = SystemClock()
    app = PlannerApp(
        settings,
        root,
        clock,
        load_master_key(settings.data_encryption_key, root),
        Path(__file__).resolve().parent / "ui",
    )
    app.serve()
    scheduler = AlarmScheduler(app.books, clock)
    scheduler.start()
    warmup = None
    if settings.ollama_model:
        warmup = OllamaWarmup(settings.ollama_base_url, settings.ollama_model)
        warmup.start()
    try:
        webview.create_window(
            "Life planner",
            f"http://127.0.0.1:{settings.port}/",
            width=1200,
            height=760,
            min_size=(900, 560),
        )
        profile = root / "webview"
        profile.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(profile))
    finally:
        if warmup is not None:
            warmup.stop()
        scheduler.stop()
        app.stop()


if __name__ == "__main__":
    main()