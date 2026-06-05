from flask import Flask, url_for


def create_app():
    app = Flask(__name__)

    @app.context_processor
    def inject_logo_url():
        return {
            "logo_url": url_for("static", filename="images/logo/logo-codebi.png")
        }

    from app.routes import main
    app.register_blueprint(main)

    return app