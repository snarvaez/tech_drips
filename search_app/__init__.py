from __future__ import annotations

from flask import Flask

from .config import Config
from .db import close_client, init_db


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config.from_object(config_object or Config)
    init_db(app)

    from .views import bp

    app.register_blueprint(bp)

    @app.cli.command("ping-db")
    def ping_db():
        from .db import ping

        ping()
        print("MongoDB ping ok")

    return app


def shutdown() -> None:
    close_client()
