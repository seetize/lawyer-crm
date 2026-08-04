import asyncio
import json
import logging
import os
import secrets
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.config import get_settings
from app.providers import build_provider
from app.providers.base import PlaceNotFoundError
from app.report_cache import MemoryReportCache
from app.service import SalonReportService
from app.review_summary import build_review_summarizer
from app.telegram_views import SECTIONS, render_section, section_keyboard
from app.user_input import parse_request
from app.catalog.db import CatalogRepository, haversine_km
from app.catalog.excel_export import build_profile_xlsx
from app.catalog.runtime import (
    build_catalog_repository,
    build_catalog_service,
    build_city_spec,
)
from app.catalog.service import CityCatalogService
from app.catalog.comparison import build_comparison_report
from app.catalog.telegram_ui import (
    category_keyboard,
    comparison_back_keyboard,
    comparison_scope_keyboard,
    locations_keyboard,
    scope_keyboard,
    stored_card_text,
    zones_keyboard,
)
from app.async_runtime import configure_asyncio_policy

router = Router()
service: SalonReportService | None = None
catalog_repository: CatalogRepository | None = None
catalog_service: CityCatalogService | None = None
catalog_tasks: set[asyncio.Task] = set()
report_cache = MemoryReportCache()
logger = logging.getLogger(__name__)

SEARCH_BUTTON = "🔎 Найти заведение"
CITY_BUTTON = "🌍 Выбрать город"
BACK_BUTTON = "⬅️ Назад"
CATALOG_BUTTON = "🏙 Городской каталог"
STATUS_BUTTON = "📊 Статус сбора"


class SearchFlow(StatesGroup):
    waiting_city = State()
    waiting_name = State()
    waiting_choice = State()


class CatalogFlow(StatesGroup):
    waiting_category = State()
    waiting_zone = State()
    waiting_location = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BUTTON)],
            [KeyboardButton(text=CITY_BUTTON)],
            [KeyboardButton(text=CATALOG_BUTTON), KeyboardButton(text=STATUS_BUTTON)],
        ],
        resize_keyboard=True,
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Просто пришлите название заведения. Регистр букв и лишние пробелы "
        "не важны.\n\n"
        "Примеры:\n"
        "GSNV lab\n"
        "GSNV-LAB Астрахань\n"
        "GSNV lab в городе Казань\n\n"
        "Расширенный формат с критериями:\n"
        "/report Название | город | рейтинг, отзывы, цены",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == CITY_BUTTON)
async def choose_city(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchFlow.waiting_city)
    await message.answer(
        "Напишите название города, например: Астрахань",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=BACK_BUTTON)]],
            resize_keyboard=True,
        ),
    )


@router.message(SearchFlow.waiting_city)
async def save_city(message: Message, state: FSMContext) -> None:
    city = " ".join((message.text or "").split())
    if city == BACK_BUTTON:
        await state.set_state(None)
        await message.answer("Возвращаюсь в главное меню.", reply_markup=main_keyboard())
        return
    if len(city) < 2:
        await message.answer("Напишите название города текстом.")
        return
    await state.update_data(city=city)
    await state.set_state(None)
    await message.answer(
        f"Город выбран: {city}",
        reply_markup=main_keyboard(),
    )


@router.message(F.text == SEARCH_BUTTON)
async def begin_search(message: Message, state: FSMContext) -> None:
    await state.set_state(SearchFlow.waiting_name)
    await message.answer(
        "Напишите название заведения или его адрес.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=BACK_BUTTON)]],
            resize_keyboard=True,
        ),
    )


@router.message(SearchFlow.waiting_name)
async def search_by_name(message: Message, state: FSMContext) -> None:
    raw = " ".join((message.text or "").split())
    if raw == BACK_BUTTON:
        await state.set_state(None)
        await message.answer("Возвращаюсь в главное меню.", reply_markup=main_keyboard())
        return
    data = await state.get_data()
    city = data.get("city", get_settings().default_city)
    if service is not None:
        try:
            async with asyncio.timeout(45):
                candidates = await service.search_candidates(raw, city)
        except Exception:
            candidates = []
        if candidates:
            token = secrets.token_hex(3)
            await state.update_data(
                search_token=token,
                search_query=raw,
                search_city=city,
                search_candidates=[candidate.model_dump() for candidate in candidates],
            )
            await state.set_state(SearchFlow.waiting_choice)
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=(
                                f"{candidate.name} · {candidate.address or 'адрес не указан'}"
                            )[:64],
                            callback_data=f"fs:{token}:{index}",
                        )
                    ]
                    for index, candidate in enumerate(candidates)
                ]
            )
            await message.answer(
                f"Найдено активных заведений: {len(candidates)}. Выберите нужное:",
                reply_markup=keyboard,
            )
            return
    await state.set_state(None)
    await send_report(message, raw, city, [], reply_markup=main_keyboard())


@router.callback_query(F.data.startswith("fs:"))
async def select_search_candidate(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    if len(parts) != 3 or parts[1] != data.get("search_token"):
        await callback.answer("Список устарел. Повторите поиск.", show_alert=True)
        return
    try:
        candidate = data["search_candidates"][int(parts[2])]
    except (KeyError, IndexError, TypeError, ValueError):
        await callback.answer("Заведение не найдено.", show_alert=True)
        return
    if callback.message is None:
        return
    await callback.answer()
    await state.set_state(None)
    await send_report(
        callback.message,
        candidate["name"],
        data.get("search_city") or get_settings().default_city,
        [],
        reply_markup=main_keyboard(),
        exact_provider_id=str(candidate["provider_id"]),
    )


@router.message(Command("report"))
async def report_command(message: Message) -> None:
    raw = (message.text or "").partition(" ")[2].strip()
    if not raw:
        await message.answer(
            "Пример: /report GSNV lab\n"
            "Или: /report Париж | Астрахань | рейтинг, отзывы, цены"
        )
        return
    parsed = parse_request(raw, get_settings().default_city)
    await send_report(
        message,
        parsed.query,
        parsed.city,
        parsed.criteria,
        reply_markup=main_keyboard(),
    )


@router.message(Command("city_status"))
@router.message(F.text == STATUS_BUTTON)
async def city_status_command(message: Message) -> None:
    if catalog_repository is None:
        await message.answer("Городской каталог ещё не инициализирован.")
        return
    city = get_settings().catalog_city
    status = await asyncio.to_thread(catalog_repository.status, city)
    jobs = ", ".join(
        f"{key}: {value}" for key, value in sorted(status["jobs"].items())
    ) or "задач пока нет"
    await message.answer(
        f"🏙 {city}\n"
        f"Уникальных заведений: {status['locations']}\n"
        f"Цифровых паспортов: {status['profiles']}\n"
        f"Задачи: {jobs}"
    )


@router.message(Command("catalog"))
@router.message(F.text == CATALOG_BUTTON)
async def catalog_command(message: Message, state: FSMContext) -> None:
    if catalog_repository is None:
        await message.answer("Городской каталог ещё не инициализирован.")
        return
    query = (
        None
        if message.text == CATALOG_BUTTON
        else (message.text or "").partition(" ")[2].strip() or None
    )
    data = await state.get_data()
    city = data.get("city") or get_settings().catalog_city
    categories = await asyncio.to_thread(
        catalog_repository.list_categories,
        city,
        query,
        15,
    )
    if not categories:
        await message.answer(
            f"В сохранённом каталоге города {city} такая группа пока не найдена. "
            "Попробуйте другое название — новый парсинг сейчас не запускается."
        )
        return
    await state.update_data(catalog_city=city)
    if query and len(categories) == 1:
        await select_catalog_category(message, state, categories[0])
        return
    await state.set_state(CatalogFlow.waiting_category)
    await message.answer(
        f"📚 Каталог · {city}\nВыберите группу или напишите её название:",
        reply_markup=category_keyboard(categories),
    )


@router.message(CatalogFlow.waiting_category)
async def catalog_category_text(message: Message, state: FSMContext) -> None:
    if catalog_repository is None:
        return
    query = " ".join((message.text or "").split())
    if query == BACK_BUTTON:
        await state.set_state(None)
        await message.answer("Возвращаюсь в главное меню.", reply_markup=main_keyboard())
        return
    data = await state.get_data()
    city = data.get("catalog_city") or get_settings().catalog_city
    categories = await asyncio.to_thread(
        catalog_repository.list_categories, city, query, 15
    )
    if not categories:
        await message.answer("Такая группа в сохранённом каталоге не найдена.")
        return
    exact = next(
        (item for item in categories if normalize_catalog_text(item["name"]) == normalize_catalog_text(query)),
        None,
    )
    if exact or len(categories) == 1:
        await select_catalog_category(message, state, exact or categories[0])
        return
    await message.answer(
        "Нашлось несколько похожих групп. Выберите нужную:",
        reply_markup=category_keyboard(categories),
    )


@router.callback_query(F.data.startswith("cg:"))
async def catalog_category_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if catalog_repository is None or callback.message is None:
        return
    category_id = (callback.data or "").partition(":")[2]
    category = await asyncio.to_thread(catalog_repository.get_category, category_id)
    if category is None:
        await callback.answer("Категория больше не доступна.", show_alert=True)
        return
    await callback.answer()
    await select_catalog_category(callback.message, state, category)


async def select_catalog_category(
    message: Message,
    state: FSMContext,
    category: dict,
) -> None:
    if catalog_repository is None:
        return
    data = await state.get_data()
    city = data.get("catalog_city") or data.get("city") or get_settings().catalog_city
    await state.update_data(
        catalog_city=city,
        catalog_category_id=category["id"],
        catalog_category_name=category["name"],
        catalog_radius_km=None,
        catalog_center_latitude=None,
        catalog_center_longitude=None,
    )
    districts, metros = await asyncio.gather(
        asyncio.to_thread(
            catalog_repository.list_zones,
            city,
            "district",
            category_id=category["id"],
        ),
        asyncio.to_thread(
            catalog_repository.list_zones,
            city,
            "metro",
            category_id=category["id"],
        ),
    )
    await state.set_state(None)
    await message.answer(
        f"{category['name']} · {city}\nВыберите зону поиска:",
        reply_markup=scope_keyboard(bool(districts), bool(metros)),
    )


@router.callback_query(F.data.startswith("cs:"))
async def catalog_scope_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    scope = (callback.data or "").partition(":")[2]
    if scope not in {"city", "district", "metro", "r1", "r5", "r10"}:
        await callback.answer("Некорректная зона.", show_alert=True)
        return
    await callback.answer()
    if scope == "city":
        await state.update_data(
            catalog_zone_type=None,
            catalog_zone_name=None,
            catalog_radius_km=None,
            catalog_center_latitude=None,
            catalog_center_longitude=None,
        )
        await show_catalog_page(callback.message, state, 0)
        return
    if scope.startswith("r"):
        radius = int(scope[1:])
        await state.update_data(
            catalog_zone_type="radius",
            catalog_zone_name=None,
            catalog_radius_km=radius,
            catalog_center_latitude=None,
            catalog_center_longitude=None,
        )
        await state.set_state(CatalogFlow.waiting_location)
        await callback.message.answer(
            f"Отправьте геопозицию — покажу сохранённые заведения в радиусе {radius} км.",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="📍 Отправить геопозицию", request_location=True)],
                    [KeyboardButton(text=BACK_BUTTON)],
                ],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
        return
    await show_zone_selection(callback.message, state, scope, action="browse")


@router.message(CatalogFlow.waiting_location, F.location)
async def catalog_location_radius(message: Message, state: FSMContext) -> None:
    if message.location is None:
        return
    data = await state.get_data()
    radius = data.get("catalog_radius_km")
    if radius not in {1, 5, 10}:
        await state.clear()
        await message.answer("Радиус поиска устарел. Откройте каталог снова.", reply_markup=main_keyboard())
        return
    await state.update_data(
        catalog_center_latitude=float(message.location.latitude),
        catalog_center_longitude=float(message.location.longitude),
    )
    await state.set_state(None)
    await message.answer("Геопозиция принята.", reply_markup=ReplyKeyboardRemove())
    await show_catalog_page(message, state, 0)


@router.message(CatalogFlow.waiting_location)
async def catalog_location_radius_text(message: Message, state: FSMContext) -> None:
    if (message.text or "") == BACK_BUTTON:
        await state.clear()
        await message.answer("Поиск рядом отменён.", reply_markup=main_keyboard())
        return
    await message.answer("Нажмите «Отправить геопозицию» или вернитесь назад.")


async def show_zone_selection(
    message: Message,
    state: FSMContext,
    zone_type: str,
    *,
    action: str,
) -> None:
    if catalog_repository is None:
        return
    data = await state.get_data()
    city = data.get("catalog_city") or get_settings().catalog_city
    zones = await asyncio.to_thread(
        catalog_repository.list_zones,
        city,
        zone_type,
        category_id=data.get("catalog_category_id") if action == "browse" else None,
    )
    if not zones:
        await state.set_state(None)
        if action == "compare":
            location_id = data.get("compare_location_id")
            markup = comparison_scope_keyboard(location_id, False, False)
        else:
            markup = scope_keyboard(False, False)
        await message.answer(
            "Для этой категории районы ещё не подтверждены картами. "
            "Выберите весь город или точный поиск рядом по геопозиции.",
            reply_markup=markup,
        )
        return
    token = secrets.token_hex(3)
    await state.update_data(
        catalog_token=token,
        catalog_zone_options=[zone["name"] for zone in zones],
        catalog_zone_type=zone_type,
        catalog_zone_action=action,
    )
    await state.set_state(CatalogFlow.waiting_zone)
    label = "станцию метро" if zone_type == "metro" else "район"
    await message.answer(
        f"Выберите {label} кнопкой или напишите название:",
        reply_markup=zones_keyboard(zones[:15], token, zone_type),
    )


@router.callback_query(F.data.startswith("ct:"))
async def catalog_typed_zone_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    if len(parts) != 3 or parts[1] != data.get("catalog_token"):
        await callback.answer("Меню устарело. Откройте каталог снова.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(CatalogFlow.waiting_zone)
    if callback.message:
        await callback.message.answer("Напишите название зоны текстом.")


@router.callback_query(F.data.startswith("cz:"))
async def catalog_zone_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    if len(parts) != 3 or parts[1] != data.get("catalog_token"):
        await callback.answer("Меню устарело. Откройте каталог снова.", show_alert=True)
        return
    try:
        zone_name = data.get("catalog_zone_options", [])[int(parts[2])]
    except (ValueError, IndexError):
        await callback.answer("Зона больше не доступна.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await apply_catalog_zone(callback.message, state, zone_name)


@router.message(CatalogFlow.waiting_zone)
async def catalog_zone_text(message: Message, state: FSMContext) -> None:
    zone_name = " ".join((message.text or "").split())
    if zone_name == BACK_BUTTON:
        await state.set_state(None)
        await message.answer("Возвращаюсь в главное меню.", reply_markup=main_keyboard())
        return
    await apply_catalog_zone(message, state, zone_name)


async def apply_catalog_zone(message: Message, state: FSMContext, zone_name: str) -> None:
    data = await state.get_data()
    action = data.get("catalog_zone_action", "browse")
    normalized = normalize_catalog_text(zone_name)
    canonical_zone = next(
        (
            value
            for value in data.get("catalog_zone_options", [])
            if normalize_catalog_text(value) == normalized
        ),
        None,
    )
    if canonical_zone is None:
        await message.answer(
            "Такой район не найден среди сохранённых данных карт. "
            "Выберите доступную кнопку или используйте поиск рядом.",
            reply_markup=(
                comparison_scope_keyboard(data.get("compare_location_id"), False, False)
                if action == "compare"
                else scope_keyboard(False, False)
            ),
        )
        return
    await state.update_data(
        catalog_zone_name=canonical_zone,
        catalog_radius_km=None,
        catalog_center_latitude=None,
        catalog_center_longitude=None,
    )
    await state.set_state(None)
    if action == "compare":
        await send_catalog_comparison(
            message,
            state,
            data.get("compare_location_id"),
            data.get("catalog_zone_type"),
            canonical_zone,
        )
        return
    await show_catalog_page(message, state, 0)


async def show_catalog_page(message: Message, state: FSMContext, page: int) -> None:
    if catalog_repository is None:
        return
    data = await state.get_data()
    token = secrets.token_hex(3)
    page_size = 8
    locations = await asyncio.to_thread(
        catalog_repository.list_locations,
        city_name=data.get("catalog_city") or get_settings().catalog_city,
        category_id=data.get("catalog_category_id"),
        category_query=(None if data.get("catalog_category_id") else data.get("catalog_category_name")),
        zone_type=data.get("catalog_zone_type"),
        zone_name=data.get("catalog_zone_name"),
        center_latitude=data.get("catalog_center_latitude"),
        center_longitude=data.get("catalog_center_longitude"),
        radius_km=data.get("catalog_radius_km"),
        limit=page_size + 1,
        offset=max(0, page) * page_size,
    )
    if not locations:
        if data.get("catalog_radius_km"):
            await message.answer(
                f"В радиусе {data['catalog_radius_km']} км сохранённых заведений не найдено. "
                "Попробуйте увеличить радиус."
            )
        else:
            await message.answer(
                "В выбранной зоне сохранённых заведений не найдено. "
                "Выберите весь город или поиск рядом."
            )
        return
    await state.update_data(catalog_token=token, catalog_page=max(0, page))
    zone = (
        f"рядом · {data['catalog_radius_km']} км"
        if data.get("catalog_radius_km")
        else data.get("catalog_zone_name") or "весь город"
    )
    await message.answer(
        f"{data.get('catalog_category_name')} · {zone}\nВыберите заведение:",
        reply_markup=locations_keyboard(
            locations[:page_size], token, max(0, page), len(locations) > page_size
        ),
    )


@router.callback_query(F.data.startswith("cp:"))
async def catalog_page_callback(callback: CallbackQuery, state: FSMContext) -> None:
    parts = (callback.data or "").split(":")
    data = await state.get_data()
    if len(parts) != 3 or parts[1] != data.get("catalog_token"):
        await callback.answer("Меню устарело. Откройте каталог снова.", show_alert=True)
        return
    try:
        page = max(0, int(parts[2]))
    except ValueError:
        page = 0
    await callback.answer()
    if callback.message:
        await show_catalog_page(callback.message, state, page)


def normalize_catalog_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


@router.callback_query(F.data.startswith("cl:"))
async def catalog_location_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.message is None:
        return
    await callback.answer()
    await state.clear()
    await send_catalog_card(callback.message, callback.from_user.id, (callback.data or "")[3:])


async def send_catalog_card(
    message: Message,
    owner_user_id: int,
    location_id: str,
) -> None:
    if catalog_repository is None:
        return
    location = await asyncio.to_thread(catalog_repository.get_location, location_id)
    if location is None:
        await message.answer("Заведение больше не найдено в активном каталоге.")
        return
    from app.models import SalonProfile, SourceRating, SourceRef

    profile_data = location.get("profile")
    if profile_data:
        profile = SalonProfile.model_validate(profile_data)
        token = await report_cache.put(owner_user_id, profile, location_id=location_id)
        rendered = render_section(profile, "main")
        await message.answer(
            rendered.text,
            reply_markup=section_keyboard(
                token,
                "main",
                compare_location_id=location_id,
            ),
            disable_web_page_preview=True,
        )
        return
    sources = location.get("sources") or []
    profile = SalonProfile(
        provider="+".join(dict.fromkeys(str(item.get("provider")) for item in sources)),
        provider_id=str(sources[0].get("provider_id") if sources else location_id),
        name=location["name"],
        address=location.get("address"),
        city=location.get("city"),
        district=location.get("district"),
        metro_stations=location.get("metros") or [],
        latitude=location.get("latitude"),
        longitude=location.get("longitude"),
        categories=location.get("categories") or [],
        ratings=[SourceRating(provider=str(item.get("provider")), rating=item.get("rating"), reviews_count=item.get("reviews_count"), url=item.get("url")) for item in sources],
        sources=[SourceRef(provider=str(item.get("provider")), provider_id=item.get("provider_id"), url=item.get("url")) for item in sources],
    )
    token = await report_cache.put(owner_user_id, profile, location_id=location_id)
    await message.answer(
        stored_card_text(location),
        reply_markup=section_keyboard(token, "main", compare_location_id=location_id),
    )


@router.callback_query(F.data.startswith("cmp:"))
async def catalog_compare_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if catalog_repository is None or callback.message is None:
        return
    location_id = (callback.data or "")[4:]
    location = await asyncio.to_thread(catalog_repository.get_location, location_id)
    if location is None:
        await callback.answer("Заведение больше не доступно.", show_alert=True)
        return
    city = location["city"]
    districts, metros = await asyncio.gather(
        asyncio.to_thread(catalog_repository.list_zones, city, "district"),
        asyncio.to_thread(catalog_repository.list_zones, city, "metro"),
    )
    await state.update_data(
        catalog_city=city,
        compare_location_id=location_id,
    )
    await callback.answer()
    await callback.message.answer(
        f"С чем сравнить «{location['name']}»? Выберите зону:",
        reply_markup=comparison_scope_keyboard(
            location_id,
            bool(districts),
            bool(metros),
        ),
    )


@router.callback_query(F.data.startswith("cms:"))
async def catalog_compare_scope_callback(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in {
        "city",
        "district",
        "metro",
        "r1",
        "r5",
        "r10",
    }:
        await callback.answer("Некорректная зона сравнения.", show_alert=True)
        return
    scope, location_id = parts[1], parts[2]
    await state.update_data(compare_location_id=location_id)
    await callback.answer()
    if callback.message is None:
        return
    if scope == "city":
        await send_catalog_comparison(callback.message, state, location_id, "city", None)
        return
    if scope.startswith("r"):
        await send_catalog_comparison(
            callback.message,
            state,
            location_id,
            "radius",
            scope[1:],
        )
        return
    await show_zone_selection(callback.message, state, scope, action="compare")


async def send_catalog_comparison(
    message: Message,
    state: FSMContext,
    location_id: str | None,
    zone_type: str | None,
    zone_name: str | None,
) -> None:
    if catalog_repository is None or not location_id:
        await message.answer("Не удалось восстановить выбранное заведение.")
        return
    location = await asyncio.to_thread(catalog_repository.get_location, location_id)
    if location is None:
        await message.answer("Заведение больше не доступно.")
        return
    features = await asyncio.to_thread(
        catalog_repository.location_features,
        location["city"],
    )
    selected = next((item for item in features if item["id"] == location_id), None)
    if selected is None:
        await message.answer("Для выбранного заведения пока недостаточно данных.")
        return
    candidates = features
    if zone_type == "district" and zone_name:
        normalized = normalize_catalog_text(zone_name)
        candidates = [
            item
            for item in features
            if normalize_catalog_text(item.get("district") or "") == normalized
        ]
    elif zone_type == "metro" and zone_name:
        normalized = normalize_catalog_text(zone_name)
        candidates = [
            item
            for item in features
            if normalized
            in {normalize_catalog_text(value) for value in item.get("metros") or ()}
        ]
    elif zone_type == "radius" and zone_name:
        radius = int(zone_name)
        if selected.get("latitude") is None or selected.get("longitude") is None:
            await message.answer("У выбранного заведения нет координат для сравнения по радиусу.")
            return
        candidates = [
            item
            for item in features
            if haversine_km(
                selected.get("latitude"),
                selected.get("longitude"),
                item.get("latitude"),
                item.get("longitude"),
            )
            <= radius
        ]
    scope_label = (
        "весь город"
        if zone_type in {None, "city"}
        else (
            f"радиус {zone_name} км"
            if zone_type == "radius"
            else f"{'метро' if zone_type == 'metro' else 'район'} {zone_name}"
        )
    )
    report = build_comparison_report(selected, candidates, scope_label)
    await state.set_state(None)
    for chunk in split_telegram_text(report):
        await message.answer(
            chunk,
            reply_markup=comparison_back_keyboard(location_id),
        )


@router.message(Command("passport"))
async def passport_command(message: Message) -> None:
    if catalog_repository is None:
        await message.answer("Городской каталог ещё не инициализирован.")
        return
    value = (message.text or "").partition(" ")[2].strip()
    if not value:
        await message.answer("Пример: /passport ID или /passport название")
        return
    location = await asyncio.to_thread(catalog_repository.get_location, value)
    if location is None:
        matches = await asyncio.to_thread(
            catalog_repository.list_locations,
            city_name=get_settings().catalog_city,
            query=value,
            limit=2,
        )
        location = (
            await asyncio.to_thread(catalog_repository.get_location, matches[0]["id"])
            if len(matches) == 1
            else None
        )
    if location is None:
        await message.answer("Нужен точный ID или однозначное название заведения.")
        return
    profile_data = location.get("profile")
    if profile_data:
        from app.models import SalonProfile

        profile = SalonProfile.model_validate(profile_data)
        owner_id = (
            message.from_user.id if message.from_user is not None else message.chat.id
        )
        token = await report_cache.put(owner_id, profile, location_id=location["id"])
        rendered = render_section(profile, "main")
        await message.answer(
            rendered.text,
            reply_markup=section_keyboard(
                token,
                "main",
                compare_location_id=location["id"],
            ),
            disable_web_page_preview=True,
        )
        return
    await message.answer(
        f"🏢 {location['name']}\n"
        f"📍 {location['address'] or 'адрес не указан'}\n"
        f"Категории: {', '.join(location['categories']) or 'не указаны'}\n"
        "Подробный паспорт ожидает фонового обогащения."
    )


@router.message(Command("competitors"))
async def competitors_command(message: Message) -> None:
    if catalog_repository is None:
        await message.answer("Городской каталог ещё не инициализирован.")
        return
    location_id = (message.text or "").partition(" ")[2].strip()
    if not location_id:
        await message.answer("Пример: /competitors ID")
        return
    location = await asyncio.to_thread(catalog_repository.get_location, location_id)
    if location is None:
        await message.answer("Заведение с таким ID не найдено.")
        return
    competitors = await asyncio.to_thread(
        catalog_repository.competitors, location_id, 10
    )
    if not competitors:
        await message.answer("Конкурентный граф ещё не рассчитан.")
        return
    lines = [f"🏆 Конкуренты · {location['name']}"]
    for index, competitor in enumerate(competitors, start=1):
        reasons = "; ".join(competitor["reasons"][:3])
        lines.append(
            f"\n{index}. {competitor['name']} — {competitor['score']:.2f}\n{reasons}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("crawl_city"))
async def crawl_city_command(message: Message) -> None:
    if not await require_catalog_admin(message) or catalog_service is None:
        return
    if any(not task.done() for task in catalog_tasks):
        await message.answer("Городской сбор уже выполняется.")
        return
    settings = get_settings()
    task = asyncio.create_task(
        catalog_service.crawl_city(
            build_city_spec(settings),
            settings.catalog_category_list(),
            force=False,
        )
    )
    track_catalog_task(task)
    await message.answer(
        f"🔄 Сбор {settings.catalog_city} запущен в фоне. Статус: /city_status"
    )


@router.message(Command("refresh_city"))
async def refresh_city_command(message: Message) -> None:
    if not await require_catalog_admin(message) or catalog_service is None:
        return
    if any(not task.done() for task in catalog_tasks):
        await message.answer("Городское обновление уже выполняется.")
        return
    settings = get_settings()
    task = asyncio.create_task(
        refresh_catalog(
            catalog_service,
            settings.catalog_enrich_batch_size,
            settings.catalog_city,
            message,
        )
    )
    track_catalog_task(task)
    await message.answer("Обновление паспортов и конкурентов запущено в фоне.")


def track_catalog_task(task: asyncio.Task) -> None:
    catalog_tasks.add(task)

    def completed(completed_task: asyncio.Task) -> None:
        catalog_tasks.discard(completed_task)
        if completed_task.cancelled():
            return
        error = completed_task.exception()
        if error is not None:
            logger.error(
                "Catalog background task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    task.add_done_callback(completed)


async def refresh_catalog(
    active_service: CityCatalogService,
    batch_size: int,
    city: str,
    message: Message,
) -> None:
    enrichment = await active_service.enrich_pending(batch_size)
    edges = await active_service.rebuild_competitors(city)
    await message.answer(
        "Обновление завершено: "
        f"паспортов {enrichment['completed']}, ошибок {enrichment['failed']}, "
        f"конкурентных связей {edges}."
    )


async def require_catalog_admin(message: Message) -> bool:
    user_id = message.from_user.id if message.from_user else None
    admin_ids = get_settings().telegram_admin_id_set()
    if not admin_ids:
        await message.answer(
            "Записывающие команды закрыты. Укажите TELEGRAM_ADMIN_IDS в .env."
        )
        return False
    if user_id not in admin_ids:
        await message.answer("Недостаточно прав для запуска городского сбора.")
        return False
    return True


@router.message()
async def plain_text(message: Message, state: FSMContext) -> None:
    if message.text:
        data = await state.get_data()
        parsed = parse_request(
            message.text,
            data.get("city", get_settings().default_city),
        )
        await send_report(
            message,
            parsed.query,
            parsed.city,
            parsed.criteria,
            reply_markup=main_keyboard(),
        )


@router.callback_query(F.data.startswith("rv:"))
async def report_section(callback: CallbackQuery) -> None:
    data = callback.data or ""
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "rv" or parts[2] not in SECTIONS:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    token, section = parts[1], parts[2]
    try:
        requested_page = int(parts[3])
    except ValueError:
        requested_page = 0
    view = await report_cache.get_view(token, callback.from_user.id)
    if view is None or callback.message is None:
        await callback.answer(
            "Отчёт устарел. Повторите поиск заведения.",
            show_alert=True,
        )
        return
    await callback.answer()
    profile = view.profile
    rendered = render_section(profile, section, requested_page)
    try:
        await callback.message.edit_text(
            rendered.text,
            reply_markup=section_keyboard(
                token,
                section,
                rendered.page,
                rendered.total_pages,
                compare_location_id=view.location_id,
            ),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


@router.callback_query(F.data.startswith("xlsx:"))
async def report_excel(callback: CallbackQuery) -> None:
    token = (callback.data or "").partition(":")[2]
    profile = await report_cache.get(token, callback.from_user.id)
    if profile is None or callback.message is None:
        await callback.answer("Отчёт устарел. Повторите поиск.", show_alert=True)
        return
    await callback.answer("Формирую Excel…")
    content = await asyncio.to_thread(build_profile_xlsx, profile)
    filename = "".join(character for character in profile.name if character.isalnum() or character in " _-").strip()[:60] or "salon"
    await callback.message.answer_document(
        BufferedInputFile(content, filename=f"{filename}.xlsx"),
        caption="Полная табличная выгрузка по заведению.",
    )


async def send_report(
    message: Message,
    query: str,
    city: str,
    criteria: list[str],
    reply_markup: ReplyKeyboardMarkup | None = None,
    exact_provider_id: str | None = None,
) -> None:
    if service is None:
        await message.answer("Сервис ещё не готов.")
        return
    # ReplyKeyboardMarkup нельзя прикреплять к сообщению, которое затем
    # редактируется: Telegram отвечает "message can't be edited".
    status = await message.answer("Собираю информацию…")

    async def replace_status(text: str) -> None:
        chunks = split_telegram_text(text)
        try:
            await status.edit_text(chunks[0], disable_web_page_preview=True)
        except TelegramBadRequest:
            try:
                await status.delete()
            except TelegramBadRequest:
                pass
            await message.answer(chunks[0], disable_web_page_preview=True)
        for chunk in chunks[1:]:
            await message.answer(chunk, disable_web_page_preview=True)

    async def restore_menu() -> None:
        if reply_markup:
            await message.answer(
                "Выберите следующее действие:",
                reply_markup=reply_markup,
            )

    try:
        async with asyncio.timeout(90):
            if exact_provider_id:
                result = await service.create_report_exact(
                    "yandex_maps",
                    exact_provider_id,
                    criteria=criteria,
                )
            else:
                result = await service.create_report(
                    query,
                    criteria=criteria,
                    city=city,
                )
        owner_id = (
            message.from_user.id
            if message.from_user is not None
            else message.chat.id
        )
        token = await report_cache.put(owner_id, result.profile)
        rendered = render_section(result.profile, "main")
        inline_keyboard = section_keyboard(token, "main")
        try:
            await status.edit_text(
                rendered.text,
                reply_markup=inline_keyboard,
                disable_web_page_preview=True,
            )
        except TelegramBadRequest:
            try:
                await status.delete()
            except TelegramBadRequest:
                pass
            await message.answer(
                rendered.text,
                reply_markup=inline_keyboard,
                disable_web_page_preview=True,
            )
        await restore_menu()
    except PlaceNotFoundError:
        await replace_status("Заведение не найдено. Добавьте город или адрес.")
        await restore_menu()
    except TimeoutError:
        await replace_status(
            "Источники отвечают слишком долго. Попробуйте повторить запрос через минуту."
        )
        await restore_menu()
    except Exception:
        logging.exception("Failed to create report")
        await replace_status("Не удалось получить данные. Попробуйте позже.")
        await restore_menu()


def split_telegram_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.splitlines():
        addition = len(line) + (1 if current else 0)
        if current and current_length + addition > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_length = 0
            chunks.extend(line[index : index + limit] for index in range(0, len(line), limit))
            continue
        current.append(line)
        current_length += len(line) + (1 if current_length else 0)
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:limit]]


async def main() -> None:
    global service, catalog_repository, catalog_service
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN в .env")
    service = SalonReportService(
        build_provider(settings),
        review_summarizer=build_review_summarizer(settings),
    )
    catalog_repository = build_catalog_repository(settings)
    catalog_service = build_catalog_service(settings, catalog_repository)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    heartbeat = asyncio.create_task(write_bot_heartbeat())
    try:
        await dispatcher.start_polling(Bot(settings.telegram_bot_token))
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def write_bot_heartbeat(interval_seconds: int = 30) -> None:
    path = Path(".harness/runs/bot-heartbeat.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        payload = {
            "pid": os.getpid(),
            "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".bot-heartbeat-",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, path)
        finally:
            Path(temporary_name).unlink(missing_ok=True)
        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    configure_asyncio_policy()
    asyncio.run(main())
