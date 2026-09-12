"""Qt localization and the machine-local language preference."""

from __future__ import annotations

import logging
from pathlib import Path
import xml.etree.ElementTree as ET

from PySide6.QtCore import QCoreApplication, QSettings, QTranslator

from app.resources import resource_path

ORGANIZATION_NAME = "Tinuvael"
APPLICATION_NAME = "SlopeForge"
LANGUAGE_KEY = "ui/language"
SUPPORTED_LANGUAGES = ("en", "ru")

logger = logging.getLogger(__name__)
_translator: QTranslator | None = None


_STANDARD_BUTTON_SOURCES = {
    "OK": "OK", "Save": "Save", "Cancel": "Cancel", "Yes": "Yes",
    "No": "No", "Close": "Close", "Discard": "Discard", "Restore": "Restore",
}

_RUSSIAN_CATALOG_RESOURCES = (
    "translations/slopeforge_ru.ts",
    "translations/slopeforge_ru_drillholes.ts",
    "translations/slopeforge_ru_theme.ts",
    "translations/slopeforge_ru_startup.ts",
    "translations/slopeforge_ru_connections.ts",
    "translations/slopeforge_ru_analysis.ts",
)


class TsTranslator(QTranslator):
    def __init__(self, parent: QCoreApplication | None = None):
        super().__init__(parent)
        self._messages: dict[tuple[str, str], str] = {}

    def _merge_catalog(self, filename: str | Path) -> bool:
        try:
            root = ET.parse(filename).getroot()
            if root.tag != "TS":
                return False
            for context_element in root.findall("context"):
                context = context_element.findtext("name", default="")
                for message in context_element.findall("message"):
                    if message.get("type") in {"obsolete", "vanished"}:
                        continue
                    source = message.findtext("source")
                    translation = message.find("translation")
                    if source is None or translation is None:
                        continue
                    if translation.get("type") in {"unfinished", "obsolete", "vanished"}:
                        continue
                    text = "".join(translation.itertext())
                    if text:
                        self._messages[(context, source)] = text
        except (OSError, ET.ParseError, ValueError):
            return False
        return True

    def load(self, filename: str | Path, *args, **kwargs) -> bool:  # noqa: ARG002
        self._messages.clear()
        if self._merge_catalog(filename):
            return True
        self._messages.clear()
        return False

    def merge(self, filename: str | Path) -> bool:
        """Merge another TS catalogue without discarding already loaded messages."""
        return self._merge_catalog(filename)

    def translate(
        self,
        context: str,
        source_text: str,
        disambiguation: str | None = None,
        n: int = -1,
    ) -> str:
        del disambiguation, n
        translated = self._messages.get((context, source_text), "")
        if translated:
            return translated
        normalized = source_text.replace("&", "").removesuffix("...")
        canonical = _STANDARD_BUTTON_SOURCES.get(normalized)
        if canonical:
            return self._messages.get(("SlopeForge", canonical), canonical)
        return ""


def settings() -> QSettings:
    return QSettings(ORGANIZATION_NAME, APPLICATION_NAME)


def normalize_language(value: object) -> str:
    code = str(value or "en").lower()
    return code if code in SUPPORTED_LANGUAGES else "en"


def selected_language(store: QSettings | None = None) -> str:
    return normalize_language((store or settings()).value(LANGUAGE_KEY, "en"))


def save_language(code: str, store: QSettings | None = None) -> str:
    normalized = normalize_language(code)
    target = store or settings()
    target.setValue(LANGUAGE_KEY, normalized)
    target.sync()
    return normalized


def install_selected_translator(app: QCoreApplication, store: QSettings | None = None) -> str:
    global _translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    language = selected_language(store)
    if language == "en":
        _translator = None
        return "en"

    base_path = resource_path(_RUSSIAN_CATALOG_RESOURCES[0])
    translator = TsTranslator(app)
    if base_path is None or not translator.load(str(base_path)):
        logger.warning("Could not load Russian TS translation; falling back to English")
        _translator = None
        return "en"

    for resource in _RUSSIAN_CATALOG_RESOURCES[1:]:
        path = resource_path(resource)
        if path is None:
            logger.warning("Optional Russian translation catalogue is missing: %s", resource)
            continue
        if not translator.merge(str(path)):
            logger.warning("Could not load optional Russian translation catalogue: %s", resource)

    app.installTranslator(translator)
    _translator = translator
    return "ru"


def tr(source: str, disambiguation: str | None = None, n: int = -1) -> str:
    translated = QCoreApplication.translate("SlopeForge", source, disambiguation, n)
    return translated or source
