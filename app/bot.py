import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import get_settings
from app.providers import build_provider
from app.providers.base import PlaceNotFoundError
from app.service import SalonReportService
from app.review_summary import build_review_summarizer
from app.user_input import parse_request

router = Router()
service: SalonReportService | None = None

SEARCH_BUTTON = "🔎 Найти заведение"
CITY_BUTTON = "🌍 Выбрать город"
BACK_BUTTON = "⬅️ Назад"


class SearchFlow(StatesGroup):
    waiting_city = State()
    waiting_name = State()


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=SEARCH_BUTTON)],
            [KeyboardButton(text=CITY_BUTTON)],
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
        async def deliver(report: str) -> None:
            await replace_status(report)

        async with asyncio.timeout(50):
            result = await service.create_report(
                query,
                criteria=criteria,
                city=city,
                deliver=deliver,
            )
        if result.report:
            await restore_menu()
            return
        await replace_status(
            "Не удалось сформировать даже частичный отчёт. Попробуйте уточнить адрес."
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
    global service
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN в .env")
    service = SalonReportService(
        build_provider(settings),
        review_summarizer=build_review_summarizer(settings),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    await dispatcher.start_polling(Bot(settings.telegram_bot_token))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
