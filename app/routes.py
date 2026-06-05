import os
from flask import Blueprint, current_app, render_template, url_for

main = Blueprint("main", __name__)


@main.route("/")
def index():
    flyers_dir = os.path.join(current_app.static_folder, "images", "flyers")
    hero_flyer_url = None

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