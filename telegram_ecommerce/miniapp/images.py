"""Bounded, shared thumbnail generation; source photographs remain untouched."""
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parents[2] / 'data' / 'demo_catalogs'


def thumbnail(relative_path, *, mark=False):
    path = (ROOT / relative_path).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise FileNotFoundError('Product photo unavailable')
    stat = path.stat()
    return _compressed(str(path), stat.st_mtime_ns, stat.st_size, mark)


@lru_cache(maxsize=128)
def _compressed(path, modified, size, mark):
    with Image.open(path) as source:
        picture = ImageOps.exif_transpose(source).convert('RGBA')
        picture.thumbnail((720, 720), Image.Resampling.LANCZOS)
        background = Image.new('RGB', picture.size, '#f0f0eb')
        background.paste(picture, mask=picture.getchannel('A'))
        picture = background
        if mark:
            ImageDraw.Draw(picture).text((12, picture.height - 22), 'CATALOG PREVIEW', fill='#525c4e')
        output = BytesIO()
        picture.save(output, 'JPEG', quality=76, optimize=True)
        return output.getvalue()
