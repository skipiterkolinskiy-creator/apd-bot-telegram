import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
STAFF_CHAT_ID = int(os.getenv("STAFF_CHAT_ID", "0"))
ADMIN_IDS = {
    int(item.strip())
    for item in os.getenv("ADMIN_IDS", "").split(",")
    if item.strip().lstrip("-").isdigit()
}
DB_PATH = os.getenv("DB_PATH", "apd_bot.db")

if not BOT_TOKEN:
    raise RuntimeError("В файле .env не указан BOT_TOKEN")
if not STAFF_CHAT_ID:
    raise RuntimeError("В файле .env не указан STAFF_CHAT_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

router = Router()


class Form(StatesGroup):
    academy_name = State()
    academy_age = State()
    academy_nickname = State()
    academy_experience = State()
    academy_reason = State()

    complaint_employee = State()
    complaint_description = State()
    complaint_evidence = State()

    thanks_employee = State()
    thanks_text = State()

    backhome_nickname = State()
    backhome_rank = State()
    backhome_reason = State()
    backhome_evidence = State()

    question_text = State()


MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎓 Академия"), KeyboardButton(text="🚨 Жалоба на сотрудника")],
        [KeyboardButton(text="⭐ Благодарность"), KeyboardButton(text="🏠 BACKHOME")],
        [KeyboardButton(text="❓ Вопросы")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите раздел APD",
)

CANCEL = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Отмена")]],
    resize_keyboard=True,
)


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_type TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'На рассмотрении',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_request(
    request_type: str,
    user_id: int,
    username: str,
    full_name: str,
    payload: str,
) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO requests
            (request_type, user_id, username, full_name, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                request_type,
                user_id,
                username,
                full_name,
                payload,
                datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_status(request_id: int, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
        conn.commit()


def get_request(request_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM requests WHERE id = ?",
            (request_id,),
        ).fetchone()


def admin_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"request:accept:{request_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"request:reject:{request_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🕓 На рассмотрении",
                    callback_data=f"request:review:{request_id}",
                )
            ],
        ]
    )


async def send_to_staff(
    bot: Bot,
    message: Message,
    request_type: str,
    title: str,
    payload: str,
    file_id: str | None = None,
    file_type: str | None = None,
) -> int:
    username = f"@{message.from_user.username}" if message.from_user.username else "не указан"
    request_id = save_request(
        request_type=request_type,
        user_id=message.from_user.id,
        username=username,
        full_name=message.from_user.full_name,
        payload=payload,
    )

    text = (
        f"<b>{escape(title)}</b>\n"
        f"<b>Номер:</b> #{request_id}\n\n"
        f"<b>Отправитель:</b> {escape(message.from_user.full_name)}\n"
        f"<b>Telegram:</b> {escape(username)}\n"
        f"<b>ID:</b> <code>{message.from_user.id}</code>\n\n"
        f"{payload}\n\n"
        f"<b>Статус:</b> На рассмотрении"
    )

    if file_id and file_type == "photo":
        await bot.send_photo(
            STAFF_CHAT_ID,
            photo=file_id,
            caption=text,
            reply_markup=admin_keyboard(request_id),
        )
    elif file_id and file_type == "video":
        await bot.send_video(
            STAFF_CHAT_ID,
            video=file_id,
            caption=text,
            reply_markup=admin_keyboard(request_id),
        )
    elif file_id and file_type == "document":
        await bot.send_document(
            STAFF_CHAT_ID,
            document=file_id,
            caption=text,
            reply_markup=admin_keyboard(request_id),
        )
    else:
        await bot.send_message(
            STAFF_CHAT_ID,
            text,
            reply_markup=admin_keyboard(request_id),
        )

    return request_id


def user_tag(message: Message) -> str:
    return f"@{message.from_user.username}" if message.from_user.username else "не указан"


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "<b>ASTRONOM POLICE DEPARTMENT</b>\n\n"
        "Добро пожаловать в официальный бот APD.\n"
        "Выберите нужный раздел в меню.",
        reply_markup=MENU,
    )


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MENU)


# -------------------- АКАДЕМИЯ --------------------

@router.message(F.text == "🎓 Академия")
async def academy_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.academy_name)
    await message.answer(
        "<b>Ускоренная Академия APD</b>\n\n"
        "Укажите имя и фамилию персонажа:",
        reply_markup=CANCEL,
    )


@router.message(Form.academy_name)
async def academy_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(Form.academy_age)
    await message.answer("Укажите возраст персонажа:")


@router.message(Form.academy_age)
async def academy_age(message: Message, state: FSMContext) -> None:
    await state.update_data(age=message.text)
    await state.set_state(Form.academy_nickname)
    await message.answer("Укажите игровой никнейм:")


@router.message(Form.academy_nickname)
async def academy_nickname(message: Message, state: FSMContext) -> None:
    await state.update_data(nickname=message.text)
    await state.set_state(Form.academy_experience)
    await message.answer("Опишите ваш опыт в RP:")


@router.message(Form.academy_experience)
async def academy_experience(message: Message, state: FSMContext) -> None:
    await state.update_data(experience=message.text)
    await state.set_state(Form.academy_reason)
    await message.answer("Почему вы хотите вступить в APD?")


@router.message(Form.academy_reason)
async def academy_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    payload = (
        f"<b>Имя персонажа:</b> {escape(data['name'])}\n"
        f"<b>Возраст:</b> {escape(data['age'])}\n"
        f"<b>Игровой ник:</b> {escape(data['nickname'])}\n"
        f"<b>Опыт RP:</b> {escape(data['experience'])}\n"
        f"<b>Причина вступления:</b> {escape(message.text)}"
    )
    request_id = await send_to_staff(
        bot, message, "academy", "🎓 Заявка в Академию APD", payload
    )
    await state.clear()
    await message.answer(
        f"Заявка <b>#{request_id}</b> отправлена руководству APD.",
        reply_markup=MENU,
    )


# -------------------- ЖАЛОБА --------------------

@router.message(F.text == "🚨 Жалоба на сотрудника")
async def complaint_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.complaint_employee)
    await message.answer(
        "Укажите имя, позывной или ник сотрудника:",
        reply_markup=CANCEL,
    )


@router.message(Form.complaint_employee)
async def complaint_employee(message: Message, state: FSMContext) -> None:
    await state.update_data(employee=message.text)
    await state.set_state(Form.complaint_description)
    await message.answer("Подробно опишите нарушение:")


@router.message(Form.complaint_description)
async def complaint_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    await state.set_state(Form.complaint_evidence)
    await message.answer(
        "Отправьте одно доказательство: фото, видео или документ.\n"
        "При отсутствии доказательств напишите: <b>Нет</b>."
    )


@router.message(Form.complaint_evidence)
async def complaint_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    file_id = None
    file_type = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    elif not message.text:
        await message.answer("Отправьте фото, видео, документ или напишите «Нет».")
        return

    payload = (
        f"<b>Сотрудник:</b> {escape(data['employee'])}\n"
        f"<b>Описание:</b> {escape(data['description'])}\n"
        f"<b>Доказательства:</b> {'прикреплены' if file_id else escape(message.text)}"
    )
    request_id = await send_to_staff(
        bot,
        message,
        "complaint",
        "🚨 Жалоба на сотрудника APD",
        payload,
        file_id,
        file_type,
    )
    await state.clear()
    await message.answer(
        f"Жалоба <b>#{request_id}</b> передана руководству.",
        reply_markup=MENU,
    )


# -------------------- БЛАГОДАРНОСТЬ --------------------

@router.message(F.text == "⭐ Благодарность")
async def thanks_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.thanks_employee)
    await message.answer(
        "Укажите имя, позывной или ник сотрудника:",
        reply_markup=CANCEL,
    )


@router.message(Form.thanks_employee)
async def thanks_employee(message: Message, state: FSMContext) -> None:
    await state.update_data(employee=message.text)
    await state.set_state(Form.thanks_text)
    await message.answer("Напишите текст благодарности:")


@router.message(Form.thanks_text)
async def thanks_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    payload = (
        f"<b>Сотрудник:</b> {escape(data['employee'])}\n"
        f"<b>Благодарность:</b> {escape(message.text)}"
    )
    request_id = await send_to_staff(
        bot, message, "thanks", "⭐ Благодарность сотруднику APD", payload
    )
    await state.clear()
    await message.answer(
        f"Благодарность <b>#{request_id}</b> отправлена.",
        reply_markup=MENU,
    )


# -------------------- BACKHOME --------------------

@router.message(F.text == "🏠 BACKHOME")
async def backhome_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.backhome_nickname)
    await message.answer(
        "<b>Программа BACKHOME</b>\n\n"
        "Укажите ваш игровой ник во время службы:",
        reply_markup=CANCEL,
    )


@router.message(Form.backhome_nickname)
async def backhome_nickname(message: Message, state: FSMContext) -> None:
    await state.update_data(nickname=message.text)
    await state.set_state(Form.backhome_rank)
    await message.answer("Укажите прежнее звание или должность:")


@router.message(Form.backhome_rank)
async def backhome_rank(message: Message, state: FSMContext) -> None:
    await state.update_data(rank=message.text)
    await state.set_state(Form.backhome_reason)
    await message.answer("Укажите причину ухода:")


@router.message(Form.backhome_reason)
async def backhome_reason(message: Message, state: FSMContext) -> None:
    await state.update_data(reason=message.text)
    await state.set_state(Form.backhome_evidence)
    await message.answer(
        "Отправьте доказательство прошлой службы: фото или документ."
    )


@router.message(Form.backhome_evidence)
async def backhome_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    file_id = None
    file_type = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"
    else:
        await message.answer("Для BACKHOME необходимо отправить фото или документ.")
        return

    payload = (
        f"<b>Игровой ник:</b> {escape(data['nickname'])}\n"
        f"<b>Прежнее звание:</b> {escape(data['rank'])}\n"
        f"<b>Причина ухода:</b> {escape(data['reason'])}\n"
        f"<b>Доказательство:</b> прикреплено"
    )
    request_id = await send_to_staff(
        bot,
        message,
        "backhome",
        "🏠 Заявка BACKHOME",
        payload,
        file_id,
        file_type,
    )
    await state.clear()
    await message.answer(
        f"Заявка BACKHOME <b>#{request_id}</b> отправлена.",
        reply_markup=MENU,
    )


# -------------------- ВОПРОСЫ --------------------

@router.message(F.text == "❓ Вопросы")
async def question_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.question_text)
    await message.answer(
        "Напишите ваш вопрос руководству APD:",
        reply_markup=CANCEL,
    )


@router.message(Form.question_text)
async def question_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    payload = f"<b>Вопрос:</b> {escape(message.text)}"
    request_id = await send_to_staff(
        bot, message, "question", "❓ Вопрос в APD", payload
    )
    await state.clear()
    await message.answer(
        f"Вопрос <b>#{request_id}</b> отправлен.",
        reply_markup=MENU,
    )


# -------------------- РЕШЕНИЯ РУКОВОДСТВА --------------------

@router.callback_query(F.data.startswith("request:"))
async def request_decision(callback: CallbackQuery, bot: Bot) -> None:
    if ADMIN_IDS and callback.from_user.id not in ADMIN_IDS:
        await callback.answer("У вас нет доступа.", show_alert=True)
        return

    _, action, request_id_raw = callback.data.split(":")
    request_id = int(request_id_raw)
    row = get_request(request_id)

    if not row:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    statuses = {
        "accept": ("Принято", "✅ Ваша заявка принята."),
        "reject": ("Отклонено", "❌ Ваша заявка отклонена."),
        "review": ("На рассмотрении", "🕓 Ваша заявка находится на рассмотрении."),
    }
    status, user_message = statuses[action]
    update_status(request_id, status)

    try:
        await bot.send_message(
            row["user_id"],
            f"{user_message}\nНомер обращения: <b>#{request_id}</b>",
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s", row["user_id"])

    old_text = callback.message.html_text or callback.message.caption or ""
    if "<b>Статус:</b>" in old_text:
        old_text = old_text.rsplit("<b>Статус:</b>", 1)[0].rstrip()

    new_text = (
        f"{old_text}\n\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Решение принял:</b> {escape(callback.from_user.full_name)}"
    )

    if callback.message.text:
        await callback.message.edit_text(
            new_text,
            reply_markup=admin_keyboard(request_id),
        )
    else:
        await callback.message.edit_caption(
            caption=new_text,
            reply_markup=admin_keyboard(request_id),
        )

    await callback.answer(f"Статус изменён: {status}")


@router.message()
async def unknown(message: Message) -> None:
    await message.answer(
        "Используйте кнопки главного меню.",
        reply_markup=MENU,
    )


async def main() -> None:
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
