# -*- coding: UTF-8 -*-
"""PDF parser that tolerates malformed image bounding boxes in pdfplumber."""

import logging
import os
from typing import List

from openjiuwen.core.retrieval.indexing.processor.parser.pdf_parser import PDFParser

logger = logging.getLogger(__name__)


class ResilientPDFParser(PDFParser):
    """Extends openjiuwen PDFParser: clamp image bboxes to page bounds, skip bad crops."""

    @staticmethod
    async def _extract_images_from_pdf_page(
        pdf_page,
        pdf_page_num: int,
        filename: str,
        output_dir: str = "images",
    ) -> List[str]:
        images = []
        px0, ptop, px1, pbottom = pdf_page.bbox
        for img_index, img in enumerate(pdf_page.images):
            try:
                x0 = float(img["x0"])
                top = float(img["top"])
                x1 = float(img["x1"])
                bottom = float(img["bottom"])
                x0 = max(px0, min(x0, px1))
                x1 = max(px0, min(x1, px1))
                top = max(ptop, min(top, pbottom))
                bottom = max(ptop, min(bottom, pbottom))
                if x1 <= x0 or bottom <= top:
                    continue
                cropped_page = pdf_page.crop((x0, top, x1, bottom))
                pil_image = cropped_page.to_image(resolution=300).original
                os.makedirs(output_dir, exist_ok=True)
                image_path = os.path.join(
                    output_dir, f"{filename}__page_{pdf_page_num}__img_{img_index}.png"
                )
                images.append(image_path)
                pil_image.save(image_path)
            except Exception as e:
                logger.warning(
                    f"[PDF] Skip image extract page={pdf_page_num} img={img_index} "
                    f"file={filename}: {e}"
                )
                continue
        return images
