"""Export the observed cross-section with the original image and coordinates."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .transect import basis, png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("offset_px", "x_px", "y_px", "luma", "marked"))
    for point in result["profile"]:
        writer.writerow((point["offset"], point["x"], point["y"], f'{point["brightness"]:.3f}', int(point["marked"])))
    return buffer.getvalue().encode()


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(412,width),height+65), "#352a21")
    output.paste(Image.fromarray(image), (0,0))
    draw = ImageDraw.Draw(output)
    cfg = card["settings"]
    cx, cy = cfg["cx"], cfg["cy"]
    forward, _ = basis(cfg)
    draw.line((cx, cy, cx+forward[0]*cfg["half_span"], cy+forward[1]*cfg["half_span"]), fill="#4a6965", width=2)
    if values := result["profile"]:
        draw.line((values[0]["x"], values[0]["y"], values[-1]["x"], values[-1]["y"]), fill="#b46f35", width=2)
    result_read = result["reading"]
    if result_read:
        for p in result_read["ends"]:
            draw.ellipse((p["x"]-4,p["y"]-4,p["x"]+4,p["y"]+4), fill="#2f584c")
        detail = f'Dark span {result_read["pixels"]} px  /  {result_read["width_mm"]:.2f} mm'
    else:
        detail = "No marked span in the visible transect"
    draw.text((14,height+14),
              f'Transverse station {result["station"]} px / lateral {result["lateral"]} px  '
              f'Axis {result["angle"]:.1f} deg', fill="#f6ead8")
    draw.text((14,height+35), detail, fill="#e0b482")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("print.png", original)
        output.writestr("reading.json", json.dumps({"card": card, "reading": result["reading"],
                                                    "center": result["center"],
                                                    "station": result["station"],
                                                    "lateral": result["lateral"]},
                                                   ensure_ascii=False, indent=2))
        output.writestr("transect-profile.csv", trace_table(result))
        output.writestr("track-proof.png", proof(image, card, result))
    return buffer.getvalue()
