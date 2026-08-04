from __future__ import annotations

import io
import zipfile
from typing import Any
from xml.etree.ElementTree import Element, SubElement, tostring

from app.models import SalonProfile


def build_profile_xlsx(profile: SalonProfile) -> bytes:
    sheets = _sheets(profile)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types(len(sheets)))
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("xl/workbook.xml", _workbook(list(sheets)))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        archive.writestr("xl/styles.xml", _styles())
        for index, rows in enumerate(sheets.values(), 1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(rows))
    return output.getvalue()


def _sheets(profile: SalonProfile) -> dict[str, list[list[Any]]]:
    return {
        "Карточка": [["Поле", "Значение"], *[[key, value] for key, value in {
            "Название": profile.name, "Адрес": profile.address, "Город": profile.city,
            "Район": profile.district, "Широта": profile.latitude, "Долгота": profile.longitude,
            "Рейтинг": profile.rating, "Количество отзывов": profile.reviews_count,
            "Описание": profile.description, "Сайт": str(profile.website or ""),
            "Карта": str(profile.map_url or ""), "Собрано": profile.collected_at.isoformat(),
        }.items()]],
        "Филиалы": [["Порядок", "Источник ID", "Название", "Адрес", "Широта", "Долгота", "URL"], *[[x.position + 1, x.provider_id, x.name, x.address, x.latitude, x.longitude, x.url] for x in profile.branches]],
        "Услуги": [["Категория", "Услуга", "Цена", "Длительность", "Источник", "URL"], *[[x.category, x.name, x.price, x.duration, x.provider, str(x.source_url or "")] for x in profile.services]],
        "Особенности": [["Категория", "Особенность", "Значение", "Источник"], *[[x.category, x.name, x.value, x.provider] for x in profile.features]],
        "Новости": [["ID", "Дата", "Текст", "Фото", "URL"], *[[x.provider_news_id, x.published_at, x.text, "\n".join(map(str, x.photos)), str(x.url or "")] for x in profile.news]],
        "Истории": [["Порядок", "Категория", "ID", "Заголовок", "Текст", "Медиа", "URL"], *[[x.position + 1, x.category, x.provider_story_id, x.title, x.text, "\n".join(x.media_urls), x.url] for x in profile.stories]],
        "Медиа": [["Порядок", "Тип", "Категория", "ID", "Исходный URL", "Локальный файл", "Описание"], *[[x.position + 1, x.media_type, x.category, x.provider_media_id, x.url, x.local_path, x.alt] for x in profile.media]],
        "Отзывы": [["Источник", "ID", "Автор", "Оценка", "Дата", "Текст", "URL"], *[[x.provider, x.provider_review_id, x.author, x.rating, x.published_at, x.text, str(x.url or "")] for x in profile.reviews]],
        "Ответы": [["Источник", "ID отзыва", "Автор", "Дата", "Ответ"], *[[x.provider, x.provider_review_id, reply.author, reply.published_at, reply.text] for x in profile.reviews for reply in x.organization_replies]],
        "Оценки": [["Источник", "Оценка", "Отзывы", "URL"], *[[x.provider, x.rating, x.reviews_count, str(x.url or "")] for x in profile.ratings]],
        "График": [["Порядок", "Значение"], *[[i, value] for i, value in enumerate(profile.opening_hours, 1)]],
        "Категории": [["Порядок", "Категория"], *[[i, value] for i, value in enumerate(profile.categories, 1)]],
        "Награды": [["Порядок", "Награда"], *[[i, value] for i, value in enumerate(profile.awards, 1)]],
        "Позиции": [["Запрос", "Позиция", "Всего", "Проверено", "Зона", "Тип зоны", "URL"], *[[x.query, x.position, x.total_results, x.checked_results, x.scope, x.scope_type, str(x.search_url or "")] for x in profile.search_rankings]],
        "Источники": [["Источник", "ID", "URL"], *[[x.provider, x.provider_id, str(x.url or "")] for x in profile.sources]],
    }


def _worksheet(rows: list[list[Any]]) -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = Element("worksheet", {"xmlns": ns})
    views = SubElement(root, "sheetViews")
    view = SubElement(views, "sheetView", {"workbookViewId": "0"})
    SubElement(view, "pane", {"ySplit": "1", "topLeftCell": "A2", "state": "frozen"})
    columns = SubElement(root, "cols")
    column_count = max((len(row) for row in rows), default=1)
    for index in range(column_count):
        width = min(50, max(12, max((len(str(row[index] or "")) for row in rows if index < len(row)), default=12) + 2))
        SubElement(columns, "col", {"min": str(index + 1), "max": str(index + 1), "width": str(width), "customWidth": "1"})
    data = SubElement(root, "sheetData")
    for row_index, values in enumerate(rows or [["Нет данных"]], 1):
        row = SubElement(data, "row", {"r": str(row_index)})
        for column_index, value in enumerate(values, 1):
            cell = SubElement(row, "c", {"r": f"{_column(column_index)}{row_index}", "t": "inlineStr", "s": "1" if row_index == 1 else "0"})
            inline = SubElement(cell, "is")
            SubElement(inline, "t").text = "" if value is None else str(value)
    if rows and rows[0]:
        SubElement(root, "autoFilter", {"ref": f"A1:{_column(len(rows[0]))}{max(1, len(rows))}"})
    return tostring(root, encoding="utf-8", xml_declaration=True)


def _column(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _content_types(count: int) -> str:
    sheets = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, count + 1))
    return f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{sheets}</Types>'


def _root_rels() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'


def _workbook(names: list[str]) -> str:
    sheets = "".join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1))
    return f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{sheets}</sheets></workbook>'


def _workbook_rels(count: int) -> str:
    sheets = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, count + 1))
    return f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{sheets}<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'


def _styles() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E78"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs></styleSheet>'
