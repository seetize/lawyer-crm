from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Review, SalonProfile
from app.report import NOT_PUBLIC

TEXT_LIMIT = 3800
SECTIONS = ("main", "reviews", "summary", "hours", "services", "booking")


@dataclass(frozen=True)
class RenderedSection:
    text: str
    page: int = 0
    total_pages: int = 1


def render_section(
    profile: SalonProfile,
    section: str,
    page: int = 0,
) -> RenderedSection:
    if section == "reviews":
        return _page(_review_blocks(profile), page)
    if section == "summary":
        return RenderedSection(render_summary(profile))
    if section == "hours":
        return RenderedSection(render_hours(profile))
    if section == "services":
        return _page(_service_blocks(profile), page)
    if section == "booking":
        return _page(_booking_blocks(profile), page)
    return RenderedSection(render_main(profile))


def render_main(profile: SalonProfile) -> str:
    rating = f"{profile.rating:.1f}/5" if profile.rating is not None else NOT_PUBLIC
    published = (
        f"{profile.reviews_count} опубликованных отзывов"
        if profile.reviews_count is not None
        else NOT_PUBLIC
    )
    lines = [
        f"🏢 {profile.name}",
        f"📍 Адрес: {profile.address or NOT_PUBLIC}",
        f"⭐ Оценка Яндекс Карт: {rating}",
        f"💬 На основной площадке: {published}",
        f"📝 Описание: {profile.description or NOT_PUBLIC}",
        "",
        f"Собрано текстов отзывов: {len(profile.reviews)}",
    ]
    if profile.ratings:
        lines.extend(["", "Оценки по источникам:"])
        for source in profile.ratings:
            value = (
                f"{source.rating:.1f}/5"
                if source.rating is not None
                else NOT_PUBLIC
            )
            count = (
                f", {source.reviews_count} отзывов/оценок"
                if source.reviews_count is not None
                else ""
            )
            lines.append(f"• {source.provider}: {value}{count}")
    if profile.map_url:
        lines.extend(["", f"Яндекс Карты: {profile.map_url}"])
    return "\n".join(lines)


def render_summary(profile: SalonProfile) -> str:
    if not profile.reviews:
        return f"🧠 Выжимка по отзывам\n\n{NOT_PUBLIC}"
    return (
        f"🧠 Выжимка по {len(profile.reviews)} собранным отзывам\n\n"
        f"{profile.reviews_summary or NOT_PUBLIC}"
    )


def render_hours(profile: SalonProfile) -> str:
    if not profile.opening_hours:
        return f"🕒 График работы\n\n{NOT_PUBLIC}"
    return "\n".join(["🕒 График работы", "", *profile.opening_hours])


def section_keyboard(
    token: str,
    active: str,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    def button(label: str, section: str) -> InlineKeyboardButton:
        prefix = "• " if active == section else ""
        return InlineKeyboardButton(
            text=prefix + label,
            callback_data=f"rv:{token}:{section}:0",
        )

    rows = [
        [button("🏢 Основное", "main")],
        [button("💬 Отзывы", "reviews"), button("🧠 Выжимка", "summary")],
        [button("🕒 График", "hours"), button("✂️ Услуги и цены", "services")],
        [button("👤 Мастера и запись", "booking")],
    ]
    if total_pages > 1 and active in {"reviews", "services", "booking"}:
        navigation: list[InlineKeyboardButton] = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton(
                    text="← Назад",
                    callback_data=f"rv:{token}:{active}:{page - 1}",
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data=f"rv:{token}:{active}:{page}",
            )
        )
        if page + 1 < total_pages:
            navigation.append(
                InlineKeyboardButton(
                    text="Далее →",
                    callback_data=f"rv:{token}:{active}:{page + 1}",
                )
            )
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _review_blocks(profile: SalonProfile) -> list[str]:
    if not profile.reviews:
        return [f"💬 Отзывы\n\n{NOT_PUBLIC}"]
    blocks = [
        (
            f"💬 Все собранные отзывы\n"
            f"Найдено текстов: {len(profile.reviews)}. "
            f"Счётчик Яндекс Карт: {profile.reviews_count or 'не указан'}."
        )
    ]
    for index, review in enumerate(profile.reviews, start=1):
        blocks.extend(_review_parts(index, review))
    return blocks


def _review_parts(index: int, review: Review) -> list[str]:
    rating = f"{review.rating:g}/5" if review.rating is not None else "без оценки"
    date = f" · {review.published_at}" if review.published_at else ""
    header = (
        f"#{index} · {review.provider}\n"
        f"{review.author} · {rating}{date}\n"
    )
    replies = ""
    for reply in review.organization_replies:
        reply_date = f" · {reply.published_at}" if reply.published_at else ""
        replies += (
            f"\n\n🏢 Ответ организации{reply_date}:\n"
            f"{reply.text}"
        )
    return _split_large_block(header + review.text + replies)


def _service_blocks(profile: SalonProfile) -> list[str]:
    if not profile.services:
        return [f"✂️ Услуги и цены\n\n{NOT_PUBLIC}"]
    blocks = [f"✂️ Услуги и цены\nНайдено позиций: {len(profile.services)}"]
    for service in profile.services:
        details = " · ".join(
            value for value in (service.price, service.duration) if value
        )
        provider = f" [{service.provider}]" if service.provider else ""
        blocks.append(
            f"• {service.name}{provider}"
            + (f"\n  {details}" if details else "")
        )
    return blocks


def _booking_blocks(profile: SalonProfile) -> list[str]:
    blocks = ["👤 Мастера и запись"]
    if profile.masters:
        blocks.append("Мастера:\n" + "\n".join(f"• {item}" for item in profile.masters))
    else:
        blocks.append(f"Мастера: {NOT_PUBLIC}")
    if profile.available_slots:
        blocks.append(
            "Ближайшее свободное время:\n"
            + "\n".join(f"• {item}" for item in profile.available_slots)
        )
    else:
        blocks.append(f"Свободное время: {NOT_PUBLIC}")
    if profile.booking_url:
        blocks.append(f"Онлайн-запись: {profile.booking_url}")
    return blocks


def _page(blocks: list[str], requested_page: int) -> RenderedSection:
    pages = _paginate(blocks)
    page = min(max(requested_page, 0), len(pages) - 1)
    return RenderedSection(pages[page], page, len(pages))


def _paginate(blocks: list[str], limit: int = TEXT_LIMIT) -> list[str]:
    pages: list[str] = []
    current: list[str] = []
    current_length = 0
    expanded: list[str] = []
    for block in blocks:
        expanded.extend(_split_large_block(block, limit))
    for block in expanded:
        addition = len(block) + (2 if current else 0)
        if current and current_length + addition > limit:
            pages.append("\n\n".join(current))
            current = []
            current_length = 0
        current.append(block)
        current_length += len(block) + (2 if current_length else 0)
    if current:
        pages.append("\n\n".join(current))
    return pages or [NOT_PUBLIC]


def _split_large_block(block: str, limit: int = TEXT_LIMIT) -> list[str]:
    if len(block) <= limit:
        return [block]
    chunk_size = limit - 40
    chunks = [
        block[index : index + chunk_size]
        for index in range(0, len(block), chunk_size)
    ]
    total = len(chunks)
    return [
        f"{chunk}\n\n(часть {index}/{total})"
        for index, chunk in enumerate(chunks, start=1)
    ]
