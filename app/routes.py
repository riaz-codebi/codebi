import os
from flask import Blueprint, current_app, render_template, url_for

main = Blueprint("main", __name__)


def get_supabase_public_url(storage, path):
    public = storage.get_public_url(path)
    return public.get("publicUrl") if isinstance(public, dict) else public


def list_supabase_files(prefixes, extensions, exact_paths=None):
    bucket = current_app.supabase.storage.from_(current_app.config["SUPABASE_BUCKET"])
    if isinstance(prefixes, str):
        prefixes = [prefixes]

    urls = []
    if exact_paths:
        for path in exact_paths:
            try:
                if bucket.exists(path):
                    public_url = get_supabase_public_url(bucket, path)
                    if public_url:
                        urls.append(public_url)
            except Exception:
                pass

        if urls:
            return sorted(urls)

    items = []
    for prefix in prefixes:
        try:
            items = bucket.list(prefix)
        except Exception:
            items = []

        if items:
            break

    if not items and hasattr(bucket, "list_v2"):
        for prefix in prefixes:
            try:
                result = bucket.list_v2({"prefix": prefix, "limit": 100})
                items = getattr(result, "objects", []) or getattr(result, "data", []) or []
            except Exception:
                items = []

            if items:
                break

    for item in items:
        name = item.get("name") if isinstance(item, dict) else None
        if name and name.lower().endswith(extensions):
            public_url = get_supabase_public_url(bucket, name)
            if public_url:
                urls.append(public_url)

    return sorted(urls)


@main.route("/")
def index():
    hero_flyer_url = None

    if current_app.supabase:
        flyer_urls = list_supabase_files(
            [
                "code-bi/marketing/flyers",
                "marketing/flyers",
                "flyers",
            ],
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"),
            exact_paths=[
                "marketing/flyers/flyer-codebi.png",
                "flyer-codebi.png",
            ],
        )
        if flyer_urls:
            hero_flyer_url = flyer_urls[0]
    else:
        flyers_dir = os.path.join(current_app.static_folder, "images", "flyers")
        if os.path.isdir(flyers_dir):
            for filename in sorted(os.listdir(flyers_dir)):
                if filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
                    hero_flyer_url = url_for("static", filename=f"images/flyers/{filename}")
                    break

    return render_template("index.html", hero_flyer_url=hero_flyer_url)


@main.route("/services")
def services():
    return render_template("services.html")


@main.route("/products")
def products():
    return render_template("products.html")


@main.route("/assets")
def assets():
    if current_app.supabase:
        flyer_urls = list_supabase_files(
            [
                "code-bi/marketing/flyers",
                "marketing/flyers",
                "flyers",
            ],
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"),
            exact_paths=[
                "marketing/flyers/flyer-codebi.png",
                "flyer-codebi.png",
            ],
        )
    else:
        flyers_dir = os.path.join(current_app.static_folder, "images", "flyers")
        flyer_urls = []

        if os.path.isdir(flyers_dir):
            for filename in sorted(os.listdir(flyers_dir)):
                if filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf")):
                    flyer_urls.append(url_for("static", filename=f"images/flyers/{filename}"))

    return render_template("assets.html", flyer_urls=flyer_urls)


@main.route("/contact")
def contact():
    return render_template("contact.html")