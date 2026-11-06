"""Cache original catalog thumbnails and make separate marked demo copies.

Install Pillow in the chosen Python environment before running this script.
Only the marked local thumbnails should be shown in a demo. Source image URLs
remain in the JSONL files for provenance, not for direct bot display.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
from pathlib import Path
import re
import time
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1] / "data" / "demo_catalogs"
FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
BRANDS = ("dailyobjects", "blue_tokai", "nicobar", "ellementry", "jaipur_rugs")


def image_bytes(url):
    for attempt in range(3):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; TelegramEcommerceDemoCatalog/1.0)"})
            with urlopen(request, timeout=25) as response:
                kind = response.headers.get("Content-Type", "")
                if not kind.startswith("image/"):
                    raise ValueError(f"Not an image: {kind}")
                data = response.read(20_000_001)
                if len(data) > 20_000_000:
                    raise ValueError("Image exceeds 20 MB")
                return data
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def mark_image(data, destination, label="DEMO"):
    with Image.open(BytesIO(data)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        width, height = image.size
        font_size = max(20, round(min(width, height) * 0.055))
        font = ImageFont.truetype(FONT, font_size) if Path(FONT).exists() else ImageFont.load_default()
        if label:
            bounds = draw.textbbox((0, 0), label, font=font)
            text_width = bounds[2] - bounds[0]
            text_height = bounds[3] - bounds[1]
            padding_x = max(10, round(font_size * 0.38))
            padding_y = max(7, round(font_size * 0.23))
            margin = max(8, round(min(width, height) * 0.025))
            right, bottom = width - margin, height - margin
            left = right - text_width - padding_x * 2
            top = bottom - text_height - padding_y * 2
            draw.rectangle((left, top, right, bottom), fill=(0, 0, 0))
            draw.text((left + padding_x - bounds[0], top + padding_y - bounds[1]), label, font=font, fill=(255, 255, 255))
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, format="JPEG", quality=86, optimize=True)


def prepare(record, rebuild_demo=False, label="DEMO"):
    brand = record["brand"]
    identifier = re.sub(r"[^a-zA-Z0-9_-]+", "_", record["source_product_id"])[:100]
    relative = f"images/{brand}/{identifier}.jpg"
    destination = ROOT / relative
    original_dir = ROOT / "images" / "original" / brand
    cached = next((p for p in original_dir.glob(identifier + ".*") if p.is_file()), None)
    errors = []
    if cached is None and rebuild_demo:
        return None, None, "Original image is not cached; rebuild mode does not use the network"
    if cached is None:
        images = record.get("images", [])
        if brand == "jaipur_rugs":
            images = sorted(images, key=lambda image: "/Headshot/" not in image["url"])
        for image in images[:3]:
            try:
                data = image_bytes(image["url"])
                with Image.open(BytesIO(data)) as source:
                    extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(source.format)
                if not extension:
                    raise ValueError("Unsupported source image format")
                cached = original_dir / f"{identifier}.{extension}"
                cached.parent.mkdir(parents=True, exist_ok=True)
                cached.write_bytes(data)
                break
            except Exception as error:
                errors.append(str(error))
    if cached is None:
        return None, None, "; ".join(errors)[:300]
    if rebuild_demo or not destination.exists() or destination.stat().st_size == 0:
        mark_image(cached.read_bytes(), destination, label)
    return relative, str(cached.relative_to(ROOT)), None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--brands", nargs="+", choices=BRANDS, default=list(BRANDS))
    parser.add_argument("--limit", type=int, help="Process only the first N records of each brand")
    parser.add_argument("--rebuild-demo", action="store_true", help="Recreate marked copies from cached originals")
    parser.add_argument("--label", default="DEMO", help="Text on the derivative; use an empty string for no label")
    args = parser.parse_args()
    for brand in args.brands:
        path = ROOT / f"{brand}.jsonl"
        records = [json.loads(line) for line in path.open(encoding="utf-8")]
        work = records[:args.limit] if args.limit else records
        failures = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(prepare, record, args.rebuild_demo, args.label): record for record in work}
            for future in as_completed(futures):
                record = futures[future]
                relative, original, error = future.result()
                if relative:
                    record["demo_image"] = relative
                    record["original_image"] = original
                else:
                    failures.append({"id": record["source_product_id"], "error": error})
        temporary = path.with_suffix(".jsonl.tmp")
        with temporary.open("w", encoding="utf-8") as file:
            for record in records:
                file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        temporary.replace(path)
        print(brand, "marked", sum(bool(r.get("demo_image")) for r in work), "/", len(work), "failures", len(failures), flush=True)
        if failures:
            (ROOT / f"{brand}_image_failures.json").write_text(json.dumps(failures, indent=2))


if __name__ == "__main__":
    main()
