import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import get_settings
from app.providers import build_provider
from app.providers.base import PlaceNotFoundError
from app.report_cache import MemoryReportCache
from app.service import SalonReportService
from app.review_summary import build_review_summarizer
from app.telegram_views import SECTIONS, render_section, section_keyboard
from app.user_input import parse_request
from app.catalog.db import CatalogRepository
from app.catalog.runtime import (
    build_catalog_repository,
    build_catalog_service,
    build_city_spec,
)
from app.catalog.service import CityCatalogService
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
    await state.set_state(None)
    await send_report(message, raw, city, [], reply_markup=main_keyboard())


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
async def catalog_command(message: Message) -> None:
    if catalog_repository is None:
        await message.answer("Городской каталог ещё не инициализирован.")
        return
    query = (
        None
        if message.text == CATALOG_BUTTON
        else (message.text or "").partition(" ")[2].strip() or None
    )
    locations = await asyncio.to_thread(
        catalog_repository.list_locations,
        city_name=get_settings().catalog_city,
        query=query,
        limit=15,
    )
    if not locations:
        await message.answer("В городском каталоге пока ничего не найдено.")
        return
    lines = [f"📚 Каталог · {get_settings().catalog_city}"]
    for location in locations:
        categories = ", ".join(location["categories"][:3]) or "без категории"
        lines.append(
            f"\n{location['name']}\n{location['address'] or 'адрес не указан'}\n"
            f"{categories}\nID: {location['id']}"
        )
    for chunk in split_telegram_text("\n".join(lines)):
        await message.answer(chunk)


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
        token = await report_cache.put(owner_id, profile)
        rendered = render_section(profile, "main")
        await message.answer(
            rendered.text,
            reply_markup=section_keyboard(token, "main"),
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
    profile = await report_cache.get(token, callback.from_user.id)
    if profile is None or callback.message is None:
        await callback.answer(
            "Отчёт устарел. Повторите поиск заведения.",
            show_alert=True,
        )
        return
    await callback.answer()
    rendered = render_section(profile, section, requested_page)
    try:
        await callback.message.edit_text(
            rendered.text,
            reply_markup=section_keyboard(
                token,
                section,
                rendered.page,
                rendered.total_pages,
            ),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error):
            raise


async def send_report(
    message: Message,
    query: str,
    city: str,
    criteria: list[str],
    reply_markup: ReplyKeyboardMarkup | None = None,
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
