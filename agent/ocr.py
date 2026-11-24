"""Apple Vision OCR for extracting text from coupon book images."""

import sys
from pathlib import Path
from typing import List, Tuple, Optional

# Apple Vision framework (macOS only)
try:
    import objc
    from Vision import VNRecognizeTextRequest, VNImageRequestHandler
    from Foundation import NSURL
except ImportError:
    sys.exit("Apple Vision framework bindings required. Install with: pip install pyobjc-framework-Vision")

from .logger import get_logger
from .config import Config


class VisionOCR:
    """OCR using Apple's Vision framework for high-accuracy text extraction."""

    def __init__(self, language: str = "en-US"):
        self.language = language
        self.logger = get_logger("ocr", Config.LOG_FILE)

    def extract_text(self, image_path: Path) -> Optional[str]:
        """Extract full text from an image file.

        Returns the extracted text or None on failure.
        """
        try:
            url = NSURL.fileURLWithPath_(str(image_path))
            request = VNRecognizeTextRequest.alloc().init()
            try:
                request.setRecognitionLanguages_([self.language])
            except Exception:
                pass

            handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)

            # performRequests returns (success_bool, error) tuple with objc.NULL for error
            result = handler.performRequests_error_([request], objc.NULL)

            if isinstance(result, tuple):
                success = result[0]
            else:
                success = result

            if not success:
                self.logger.warning(f"OCR failed for: {image_path}")
                return None

            # Get the request results from the original request object
            observations = request.results()

            lines = []
            for observation in observations:
                candidates = observation.topCandidates_(1)
                if candidates:
                    lines.append(candidates[0].string())

            return "\n".join(lines)

        except Exception as e:
            self.logger.error(f"OCR error on {image_path}: {e}")
            return None

    def extract_text_with_regions(self, image_path: Path) -> List[Tuple[str, dict]]:
        """Extract text with region information for segmentation.

        Returns list of (text, region_info) tuples where region_info contains
        bounding box coordinates relative to the image.
        """
        try:
            url = NSURL.fileURLWithPath_(str(image_path))
            request = VNRecognizeTextRequest.alloc().init()
            try:
                request.setRecognitionLanguages_([self.language])
            except Exception:
                pass

            handler = VNImageRequestHandler.alloc().initWithURL_options_(url, None)

            result = handler.performRequests_error_([request], objc.NULL)
            if isinstance(result, tuple):
                success = result[0]
            else:
                success = result

            if not success:
                return []

            observations = request.results()

            regions = []
            for observation in observations:
                candidates = observation.topCandidates_(1)
                if candidates:
                    text = candidates[0].string()
                    bounding_box = observation.boundingBox()
                    region_info = {
                        "x": float(bounding_box.origin.x),
                        "y": float(bounding_box.origin.y),
                        "width": float(bounding_box.size.width),
                        "height": float(bounding_box.size.height),
                    }
                    regions.append((text, region_info))

            return regions

        except Exception as e:
            self.logger.error(f"Region extraction error on {image_path}: {e}")
            return []


class OCRService:
    """OCR service with caching and fallback capabilities."""

    def __init__(self, cache_dir: Path):
        self.ocr = VisionOCR()
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.text_cache = {}  # In-memory cache for current session
        self.logger = get_logger("ocr_service", Config.LOG_FILE)

    def _get_cache_path(self, image_path: Path) -> Path:
        """Get the OCR text cache file path for an image."""
        return self.cache_dir / f"{image_path.stem}.txt"

    def extract_text(self, image_path: Path, use_cache: bool = True) -> Optional[str]:
        """Extract text from image with caching.

        Checks in-memory cache first, then disk cache, then performs OCR.
        """
        # Check in-memory cache
        str_path = str(image_path)
        if str_path in self.text_cache:
            return self.text_cache[str_path]

        # Check disk cache
        if use_cache:
            cache_path = self._get_cache_path(image_path)
            if cache_path.exists():
                try:
                    text = cache_path.read_text(encoding="utf-8")
                    self.text_cache[str_path] = text
                    return text
                except Exception as e:
                    self.logger.warning(f"Cache read error: {e}")

        # Perform OCR
        text = self.ocr.extract_text(image_path)
        if text is not None:
            # Cache in memory and on disk
            self.text_cache[str_path] = text
            try:
                cache_path = self._get_cache_path(image_path)
                cache_path.write_text(text, encoding="utf-8")
            except Exception as e:
                self.logger.warning(f"Cache write error: {e}")

        return text

    def clear_cache(self):
        """Clear the in-memory cache."""
        self.text_cache.clear()
