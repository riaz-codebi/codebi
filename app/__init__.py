import os
from dotenv import load_dotenv
from flask import Flask, url_for
from supabase import create_client

load_dotenv()

def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-me")
    app.config["SUPABASE_URL"] = os.getenv("SUPABASE_URL")
    app.config["SUPABASE_KEY"] = os.getenv("SUPABASE_KEY")
    app.config["SUPABASE_BUCKET"] = os.getenv("SUPABASE_BUCKET", "Code-BI")

    supabase_url = app.config["SUPABASE_URL"]
    supabase_key = app.config["SUPABASE_KEY"]

    if supabase_url and supabase_key:
        app.supabase = create_client(supabase_url, supabase_key)
    else:
        app.supabase = None

    def _resolve_logo_url(bucket, key):
        try:
            if bucket.exists(key):
                public = bucket.get_public_url(key)
                return public.get("publicUrl") if isinstance(public, dict) else public
        except Exception as exc:
            app.logger.debug("Supabase logo resolution failed for %s: %s", key, exc)
        return None

    @app.context_processor
    def inject_logo_url():
        logo_url = None

        if app.supabase:
            bucket = app.supabase.storage.from_(app.config["SUPABASE_BUCKET"])
            for key in [
                "code-bi/brand/logo/logo-codebi.png",
                "logo/logo-codebi.png",
                "brand/logo/logo-codebi.png",
            ]:
                logo_url = _resolve_logo_url(bucket, key)
                if logo_url:
                    break

        return {
            "logo_url": logo_url
        }

    from app.routes import main
    app.register_blueprint(main)

    return app