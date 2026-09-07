"""PDF parser skill using PyMuPDF for text + image extraction."""
import os
import hashlib
from pathlib import Path

SKILL = {
    "name": "PDF",
    "description": "Extract text and images from PDF files",
    "extensions": [".pdf"],
    "mime_types": ["application/pdf"],
}

IMAGE_MIN_SIZE = 80  # Minimum width/height to keep (skip tiny icons)

async def parse(file_path: str = "", data: bytes = None):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf

    doc = pymupdf.open(file_path)
    records = []
    images = []
    seen_hashes = set()

    # Determine image output directory
    images_dir = Path(file_path).parent.parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    doc_stem = Path(file_path).stem

    for page in doc:
        page_num = page.number + 1

        # Extract text
        text = page.get_text() or ""
        if text.strip():
            records.append({"page": page_num, "text": text.strip()})

        # Extract images from this page
        img_list = page.get_images(full=True)
        for img_info in img_list:
            xref = img_info[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)

                # Skip tiny images (icons, lines, dots)
                if pix.width < IMAGE_MIN_SIZE or pix.height < IMAGE_MIN_SIZE:
                    pix = None
                    continue

                # Skip duplicates by content hash
                img_data = pix.tobytes("png")
                h = hashlib.md5(img_data).hexdigest()
                if h in seen_hashes:
                    pix = None
                    continue
                seen_hashes.add(h)

                # Save image
                img_name = "%s_p%d_%s.png" % (doc_stem, page_num, h[:8])
                img_path = images_dir / img_name
                with open(img_path, "wb") as f:
                    f.write(img_data)

                images.append({
                    "page": page_num,
                    "filename": img_name,
                    "path": str(img_path),
                    "width": pix.width,
                    "height": pix.height,
                    "relative_path": "images/%s" % img_name,
                })

                pix = None
            except Exception:
                pass

    doc.close()

    return {
        "records": records,
        "fields": ["page", "text"],
        "metadata": {
            "page_count": len(records),
            "image_count": len(images),
            "format": "pdf",
        },
        "images": images,
    }
