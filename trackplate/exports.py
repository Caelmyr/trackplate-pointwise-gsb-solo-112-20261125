"""Export the observed cross-section with the original image and coordinates."""

from io import BytesIO, StringIO
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import json

from PIL import Image, ImageDraw

from .transect import basis, pad_analysis, pad_runs, png


def trace_table(result):
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("offset_px", "x_px", "y_px", "luma", "marked", "palm_pad"))
    for point in result["profile"]:
        writer.writerow((point["offset"], point["x"], point["y"], f'{point["brightness"]:.3f}',
                         int(point["marked"]), int(point["pad"])))
    return buffer.getvalue().encode()


def segments_line(reading, unit="px"):
    if not reading or not reading["segments"]:
        return "No palm-pad segment in the visible transect"
    parts = []
    for segment in reading["segments"]:
        if unit == "mm":
            parts.append(f'#{segment["index"] + 1} {segment["width_mm"]:.2f} mm')
        else:
            parts.append(f'#{segment["index"] + 1} {segment["pixels"]} px')
    if reading.get("mode") == "pad":
        parts.append(f'Total {reading["pixels"]} px')
    return " / ".join(parts)


def proof(image, card, result):
    height, width = image.shape[:2]
    output = Image.new("RGB", (max(560, width), height+88), "#352a21")
    output.paste(Image.fromarray(image), (0, 0))
    draw = ImageDraw.Draw(output)
    cfg = card["settings"]
    cx, cy = cfg["cx"], cfg["cy"]
    forward, _ = basis(cfg)
    # Cavity overlay: only accepted dark print, light seams and interior white pores stay clear.
    pad, area = pad_analysis(image, cfg)
    for row, start, stop in pad_runs(pad, area["region"]):
        draw.line((start, row, stop - 1, row), fill="#2f584c", width=1)
    draw.line((cx, cy, cx+forward[0]*cfg["half_span"], cy+forward[1]*cfg["half_span"]), fill="#4a6965", width=2)
    if values := result["profile"]:
        draw.line((values[0]["x"], values[0]["y"], values[-1]["x"], values[-1]["y"]), fill="#b46f35", width=2)
    reading = result["reading"]
    if reading:
        for index, segment in enumerate(reading.get("segments", [])):
            start, end = segment["ends"]
            midpoint = {"x": (start["x"] + end["x"]) / 2, "y": (start["y"] + end["y"]) / 2}
            for p in (start, end):
                draw.ellipse((p["x"]-5, p["y"]-5, p["x"]+5, p["y"]+5), fill="#2f584c")
                draw.ellipse((p["x"]-2, p["y"]-2, p["x"]+2, p["y"]+2), fill="#f4dfb4")
            label_y = midpoint["y"] - 26 if index % 2 == 0 else midpoint["y"] + 9
            draw.text((midpoint["x"] + 5, label_y), f'#{index + 1}', fill="#f4dfb4")
        detail = (f'Pad width {reading["pixels"]} px  /  {reading["width_mm"]:.2f} mm'
                  if reading["mode"] == "pad" else
                  f'Continuous span {reading["pixels"]} px  /  {reading["width_mm"]:.2f} mm')
        segments = segments_line(reading)
    else:
        detail = "No palm-pad segment in the visible transect"
        segments = ""
    area = result.get("area", {})
    draw.text((14,height+12), f'Transverse station {result["station"]} px  Axis {result["angle"]:.1f} deg', fill="#f6ead8")
    draw.text((14,height+32), detail, fill="#e0b482")
    draw.text((14,height+52), f'Contact area {area.get("pixels", 0)} px  /  {area.get("mm2", 0):.2f} mm2', fill="#e0b482")
    draw.text((14,height+70), f'Transect segments: {segments}' if segments else segments, fill="#d9c3a8")
    return png(output)


def package(card, original, image, result):
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as output:
        output.writestr("print.png", original)
        output.writestr("reading.json", json.dumps({"card": card, "reading": result["reading"],
                                                    "area": result.get("area"),
                                                    "center": result["center"]}, ensure_ascii=False, indent=2))
        output.writestr("transect-profile.csv", trace_table(result))
        output.writestr("track-proof.png", proof(image, card, result))
    return buffer.getvalue()
