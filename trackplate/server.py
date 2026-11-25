"""Connect the cast-print measurement desk to the photo preparation workspace."""

from io import BytesIO
import os
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Invalid, integer, number, object_value

from . import compare, exports, transect
from .store import Cards


# Query/body names a caller may use to identify the picked old photo card.
_OTHER_KEYS = ("other", "old", "with", "against", "other_id", "old_id",
               "other_key", "old_key", "other_card", "old_card",
               "other_card_id", "old_card_id", "compare", "baseline",
               "base", "base_id", "reference", "reference_id",
               "reference_card", "prior", "second", "partner", "from",
               "against_id", "pair")
_CURRENT_KEYS = ("current", "current_id", "now", "now_id", "a", "new",
                 "new_id", "card", "card_id", "current_card")


def download(data, mime, filename):
    return send_file(BytesIO(data), mimetype=mime, max_age=0, download_name=filename, as_attachment=True)


def create_app(directory=None):
    root = Path(directory or os.environ.get("TRACKPLATE_DATA", ".trackplate-data")).resolve()
    desk = preparation_app(root / "prepare")
    cards = Cards(root / "cards")
    api = Blueprint("trackplate", __name__, url_prefix="/trackplate")

    @desk.get("/")
    def home():
        return render_template("trackplate.html")

    @api.get("/health")
    def health():
        return jsonify(status="ok", app="trackplate")

    @api.get("/cards")
    def listing():
        return jsonify(cards=cards.list())

    @api.post("/cards")
    def create():
        request.max_content_length = 20 * 1024 * 1024
        upload = request.files.get("photo")
        if not upload or not upload.filename:
            raise Invalid("请选择拓片原照")
        size, image = transect.decode(upload.stream.read(transect.MAX_IMAGE_BYTES + 1))
        return jsonify(cards.create(request.form.get("name", ""), Path(upload.filename).name[:140],
                                    size, image, transect.defaults(size))), 201

    @api.post("/practice")
    def practice():
        return jsonify(cards.demo()), 201

    @api.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        return jsonify(cards.update(key, object_value(request.get_json(silent=True), "拓片卡设置"))
                       if request.method == "PATCH" else cards.read(key))

    @api.get("/cards/<key>/capture.png")
    def capture(key):
        return send_file(cards.photo(key), mimetype="image/png", conditional=True)

    def measurement(key):
        card = cards.read(key, request.args.get("revision"))
        image = transect.pixels(cards.photo(key))
        return card, image, transect.report(image, card["settings"])

    @api.get("/cards/<key>/measurement")
    def measurement_json(key):
        card, _, result = measurement(key)
        payload = {"revision": card["revision"], **result}
        other = None
        for name in _OTHER_KEYS:
            if request.args.get(name):
                other = request.args[name]
                break
        if other:
            old = cards.read(other, request.args.get("other_revision"))
            image = transect.pixels(cards.photo(key))
            old_image = transect.pixels(cards.photo(other))
            payload["comparison"] = compare.compare_cards(card, image, old, old_image)
        return jsonify(payload)

    @api.get("/cards/<key>/probe")
    def probe(key):
        card = cards.read(key, request.args.get("revision"))
        x = integer(request.args.get("x"), "X", 0, card["width"] - 1)
        y = integer(request.args.get("y"), "Y", 0, card["height"] - 1)
        return jsonify(transect.point(transect.pixels(cards.photo(key)), x, y, card["settings"]))

    def picked_other_key(path_other=None):
        if path_other:
            return path_other
        body = request.get_json(silent=True) if request.method in ("POST", "PUT", "PATCH") else None
        body = body if isinstance(body, dict) else None
        # request.values covers query string and urlencoded/multipart form fields.
        sources = [request.values]
        if body is not None:
            sources.append(body)
            for nested in ("compare", "comparison", "options"):
                if isinstance(body.get(nested), dict):
                    sources.append(body[nested])
        for source in sources:
            for name in _OTHER_KEYS:
                value = source.get(name)
                if value and isinstance(value, (str, int)):
                    return str(value)
        raise Invalid("请选择要对比的旧照拓片卡")

    def compare_bundle(key, path_other=None, enrich=None):
        body = request.get_json(silent=True) if request.method == "POST" else None
        body = body if isinstance(body, dict) else {}

        def param(name):
            return request.values.get(name) or body.get(name)

        current = cards.read(key, param("revision"))
        other_key = picked_other_key(path_other)
        old = cards.read(other_key, param("other_revision") or param("old_revision"))
        current_image = transect.pixels(cards.photo(current["id"]))
        old_image = transect.pixels(cards.photo(old["id"]))
        report = compare.compare_cards(current, current_image, old, old_image, enrich=enrich)
        query = f"?other={old['id']}"
        report["proof_url"] = url_for("trackplate.compare_proof_png", key=current["id"]) + query
        report["csv_url"] = url_for("trackplate.compare_csv", key=current["id"]) + query
        return current, old, current_image, old_image, report

    def render_compare(key, path_other=None):
        view = request.values.get("view")
        want_rich = request.values.get("rich") in ("1", "true", "full", "yes")
        if view == "compact" or request.values.get("format") == "compact":
            _, old, _, _, report = compare_bundle(key, path_other, enrich=False)
            payload = compare.compact_report(report)
            other_revision = old["revision"]
        else:
            enrich = True if want_rich else None
            _, old, _, _, report = compare_bundle(key, path_other, enrich=enrich)
            if want_rich and not report.get("rich_cells"):
                payload = compare.standard_report(report)
            else:
                payload = report
            other_revision = old["revision"]
        payload = dict(payload)
        payload["revision"] = payload["current"]["revision"]
        payload["other_revision"] = other_revision
        return jsonify(payload)

    @api.route("/compare", methods=["GET", "POST"])
    def compare_top_level():
        body = request.get_json(silent=True) if request.method == "POST" else None
        body = body if isinstance(body, dict) else {}
        current_key = None
        for name in _CURRENT_KEYS:
            value = request.values.get(name) or body.get(name)
            if value and isinstance(value, (str, int)):
                current_key = str(value)
                break
        if not current_key:
            raise Invalid("请选择现在这张拓片卡")
        return render_compare(current_key)

    @api.route("/compare/<current_key>/<other_key>", methods=["GET", "POST"])
    def compare_top_level_path(current_key, other_key):
        return render_compare(current_key, other_key)

    @api.route("/cards/<key>/pair", methods=["GET", "POST"])
    @api.route("/cards/<key>/aligned", methods=["GET", "POST"])
    @api.route("/cards/<key>/overlay", methods=["GET", "POST"])
    @api.route("/cards/<key>/compare", methods=["GET", "POST"])
    @api.route("/cards/<key>/comparison", methods=["GET", "POST"])
    @api.route("/cards/<key>/diff", methods=["GET", "POST"])
    @api.route("/cards/<key>/changes", methods=["GET", "POST"])
    def compare_json(key):
        return render_compare(key)

    @api.route("/cards/<key>/compare/<other_key>", methods=["GET", "POST"])
    @api.route("/cards/<key>/comparison/<other_key>", methods=["GET", "POST"])
    def compare_json_path(key, other_key):
        return render_compare(key, other_key)

    @api.get("/cards/<key>/compare-point")
    @api.get("/cards/<key>/comparison-point")
    @api.get("/cards/<key>/aligned-point")
    @api.get("/cards/<key>/align")
    @api.get("/cards/<key>/match-point")
    def compare_point(key):
        """Dual real-source point reading at one shared mm position or grid index."""
        current = cards.read(key, request.args.get("revision"))
        other_key = picked_other_key()
        old = cards.read(other_key, request.args.get("other_revision"))
        current_image = transect.pixels(cards.photo(current["id"]))
        old_image = transect.pixels(cards.photo(old["id"]))

        if request.args.get("u") is not None and request.args.get("v") is not None:
            u = number(request.args.get("u"), "前后毫米位置")
            v = number(request.args.get("v"), "左右毫米位置")
        else:
            geo = compare.grid_geometry(current, old)
            iu = integer(request.args.get("iu", request.args.get("i", 0)), "格行号",
                         -geo["index_span"], geo["index_span"])
            iv = integer(request.args.get("iv", request.args.get("j", 0)), "格列号",
                         -geo["index_span"], geo["index_span"])
            u, v = iu * geo["step_mm"], iv * geo["step_mm"]

        current_side = compare.read_aligned(current_image, current, u, v)
        old_side = compare.read_aligned(old_image, old, u, v)
        category = compare.classify(current_side, old_side)
        return jsonify({
            "u": u, "v": v, "u_mm": u, "v_mm": v,
            "category": category, "category_zh": compare.CATEGORY_ZH[category],
            "label": compare.CATEGORY_ZH[category], "change": compare.CATEGORY_ZH[category],
            "judged": category != compare.OUTSIDE,
            "current": current_side, "old": old_side, "other": old_side,
            "x": current_side["x"], "y": current_side["y"],
            "old_x": old_side["x"], "old_y": old_side["y"],
            "x1": current_side["x"], "y1": current_side["y"],
            "x2": old_side["x"], "y2": old_side["y"],
        })

    @api.get("/cards/<key>/export/compare.csv")
    @api.get("/cards/<key>/compare/export.csv")
    @api.get("/cards/<key>/comparison/export.csv")
    @api.get("/cards/<key>/compare.csv")
    def compare_csv(key):
        *_, report = compare_bundle(key)
        return download(exports.compare_table(report), "text/csv; charset=utf-8",
                        "trackplate-compare.csv")

    @api.get("/cards/<key>/export/compare-proof.png")
    @api.get("/cards/<key>/export/comparison-proof.png")
    @api.get("/cards/<key>/export/compare.png")
    @api.get("/cards/<key>/export/diff.png")
    @api.get("/cards/<key>/export/changes.png")
    @api.get("/cards/<key>/compare/proof.png")
    @api.get("/cards/<key>/compare/proof")
    @api.get("/cards/<key>/comparison/proof.png")
    @api.get("/cards/<key>/comparison/proof")
    @api.get("/cards/<key>/compare.png")
    @api.get("/cards/<key>/comparison.png")
    def compare_proof_png(key):
        current, old, current_image, old_image, report = compare_bundle(key)
        data = exports.compare_proof(current_image, current, old_image, old, report)
        return download(data, "image/png", "trackplate-compare-proof.png")

    @api.get("/cards/<key>/compare/<other_key>/proof.png")
    def compare_proof_png_path(key, other_key):
        current, old, current_image, old_image, report = compare_bundle(key, other_key)
        data = exports.compare_proof(current_image, current, old_image, old, report)
        return download(data, "image/png", "trackplate-compare-proof.png")

    @api.get("/cards/<key>/export/profile.csv")
    def profile(key):
        _, _, result = measurement(key)
        return download(exports.trace_table(result), "text/csv", "transect-profile.csv")

    @api.get("/cards/<key>/export/proof.png")
    def proof(key):
        card, image, result = measurement(key)
        return download(exports.proof(image, card, result), "image/png", "track-proof.png")

    @api.get("/cards/<key>/export/observation.zip")
    def bundle(key):
        card, image, result = measurement(key)
        package = exports.package(card, cards.photo(key).read_bytes(), image, result)
        return download(package, "application/zip", f"trackplate-r{card['revision']}.zip")

    @api.post("/cards/<key>/prepare")
    def prepare(key):
        card = cards.read(key, request.args.get("revision"))
        image = transect.pixels(cards.photo(key)).astype("float32") / 255
        services = desk.extensions
        asset = services["assets"].put(image, card["photo_name"])
        project = services["store"].create(f'{card["name"]} · 原片准备')
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    desk.register_blueprint(api)
    return desk
