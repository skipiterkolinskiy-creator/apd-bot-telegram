import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
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
LOGO_PATH = Path(os.getenv("LOGO_PATH", "APD.png"))
DEFAULT_STAFF_PREFIX = os.getenv(
    "DEFAULT_STAFF_PREFIX",
    "Сотрудник руководства",
).strip()

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


class AdminReply(StatesGroup):
    waiting_text = State()


MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎓 Академия"), KeyboardButton(text="🚨 Жалоба")],
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
                created_at TEXT NOT NULL,
                answer TEXT,
                answered_prefix TEXT,
                answered_at TEXT
            )
            """
        )

        columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
        if "answer" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN answer TEXT")
        if "answered_prefix" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN answered_prefix TEXT")
        if "answered_at" not in columns:
            conn.execute("ALTER TABLE requests ADD COLUMN answered_at TEXT")
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


def get_request(request_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT * FROM requests WHERE id = ?",
            (request_id,),
        ).fetchone()


def update_status(request_id: int, status: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
        conn.commit()


def save_answer(
    request_id: int,
    answer: str,
    prefix: str,
) -> str:
    answered_at = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE requests
            SET answer = ?,
                answered_prefix = ?,
                answered_at = ?,
                status = 'Закрыто'
            WHERE id = ?
            """,
            (answer, prefix, answered_at, request_id),
        )
        conn.commit()
    return answered_at


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


async def get_chat_prefix(bot: Bot, user_id: int) -> str:
    """
    Берёт пользовательский титул администратора именно из STAFF_CHAT_ID.
    Например: "Шеф Департамента", "Заместитель Шефа".
    Имя пользователя нигде не добавляется.
    """
    try:
        member = await bot.get_chat_member(STAFF_CHAT_ID, user_id)
        custom_title = getattr(member, "custom_title", None)
        if custom_title and custom_title.strip():
            return custom_title.strip()

        status = str(getattr(member, "status", ""))
        if "creator" in status:
            return "Владелец руководящего состава"
        if "administrator" in status:
            return DEFAULT_STAFF_PREFIX
    except Exception:
        logging.exception("Не удалось получить префикс участника из чата")

    return DEFAULT_STAFF_PREFIX


def default_admin_keyboard(request_id: int) -> InlineKeyboardMarkup:
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


def question_admin_keyboard(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💬 Ответить",
                    callback_data=f"question:reply:{request_id}",
                ),
                InlineKeyboardButton(
                    text="🔒 Закрыть",
                    callback_data=f"question:close:{request_id}",
                ),
            ]
        ]
    )


def format_header(title: str, request_id: int) -> str:
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>{escape(title)}</b>\n"
        f"<b>Номер обращения:</b> APD-{request_id:06d}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
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
    username = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "не указан"
    )

    request_id = save_request(
        request_type=request_type,
        user_id=message.from_user.id,
        username=username,
        full_name=message.from_user.full_name,
        payload=payload,
    )

    text = (
        format_header(title, request_id)
        + f"<b>Отправитель:</b> {escape(message.from_user.full_name)}\n"
        + f"<b>Telegram:</b> {escape(username)}\n"
        + f"<b>ID:</b> <code>{message.from_user.id}</code>\n\n"
        + payload
        + "\n\n━━━━━━━━━━━━━━━━━━━━\n"
        + "<b>Статус:</b> На рассмотрении"
    )

    keyboard = (
        question_admin_keyboard(request_id)
        if request_type == "question"
        else default_admin_keyboard(request_id)
    )

    if file_id and file_type == "photo":
        await bot.send_photo(
            STAFF_CHAT_ID,
            file_id,
            caption=text,
            reply_markup=keyboard,
        )
    elif file_id and file_type == "video":
        await bot.send_video(
            STAFF_CHAT_ID,
            file_id,
            caption=text,
            reply_markup=keyboard,
        )
    elif file_id and file_type == "document":
        await bot.send_document(
            STAFF_CHAT_ID,
            file_id,
            caption=text,
            reply_markup=keyboard,
        )
    else:
        await bot.send_message(
            STAFF_CHAT_ID,
            text,
            reply_markup=keyboard,
        )

    return request_id


async def show_start(message: Message, state: FSMContext) -> None:
    await state.clear()

    caption = (
        "<b>ПОЛИЦЕЙСКИЙ ДЕПАРТАМЕНТ АСТРОНОМА</b>\n"
        "<b>ОФИЦИАЛЬНЫЙ БОТ APD</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вы находитесь в официальной информационной системе "
        "<b>Astronom Police Department</b>.\n\n"
        "APD обеспечивает общественную безопасность города Астроном, "
        "реагирует на происшествия, рассматривает обращения граждан, "
        "проводит набор в Академию и поддерживает связь с бывшими сотрудниками.\n\n"
        "<b>Через этого бота вы можете:</b>\n"
        "🎓 Подать заявление в ускоренную Академию\n"
        "🚨 Подать жалобу на сотрудника\n"
        "⭐ Оставить благодарность\n"
        "🏠 Подать заявку по программе BACKHOME\n"
        "❓ Задать вопрос руководству\n\n"
        "Каждое обращение получает индивидуальный номер и передаётся "
        "непосредственно руководству департамента.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>ЧЕСТЬ • ГОРДОСТЬ • ДОСТОИНСТВО</b>\n"
        "<i>Служим закону и людям.</i>"
    )

    if LOGO_PATH.exists():
        await message.answer_photo(
            photo=FSInputFile(LOGO_PATH),
            caption=caption,
            reply_markup=MENU,
        )
    else:
        await message.answer(caption, reply_markup=MENU)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await show_start(message, state)


@router.message(Command("cancel"))
@router.message(F.text == "❌ Отмена")
async def cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=MENU)


# ---------------- АКАДЕМИЯ ----------------

@router.message(F.text == "🎓 Академия")
async def academy_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.academy_name)
    await message.answer(
        "<b>УСКОРЕННАЯ АКАДЕМИЯ APD</b>\n\n"
        "Укажите имя и фамилию персонажа:",
        reply_markup=CANCEL,
    )


@router.message(Form.academy_name)
async def academy_name(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text.split()) < 2:
        await message.answer("Укажите имя и фамилию персонажа двумя словами.")
        return
    await state.update_data(name=text)
    await state.set_state(Form.academy_age)
    await message.answer("Укажите возраст персонажа числом:")


@router.message(Form.academy_age)
async def academy_age(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit() or not 14 <= int(text) <= 90:
        await message.answer("Возраст должен быть числом от 14 до 90.")
        return
    await state.update_data(age=text)
    await state.set_state(Form.academy_nickname)
    await message.answer("Укажите игровой никнейм:")


@router.message(Form.academy_nickname)
async def academy_nickname(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 3 or len(text) > 32:
        await message.answer("Игровой ник должен содержать от 3 до 32 символов.")
        return
    await state.update_data(nickname=text)
    await state.set_state(Form.academy_experience)
    await message.answer("Опишите ваш опыт в RP минимум в 15 символах:")


@router.message(Form.academy_experience)
async def academy_experience(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 15:
        await message.answer("Ответ слишком короткий. Напишите подробнее.")
        return
    await state.update_data(experience=text)
    await state.set_state(Form.academy_reason)
    await message.answer("Почему вы хотите вступить в APD? Минимум 20 символов.")


@router.message(Form.academy_reason)
async def academy_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer("Ответ слишком короткий. Напишите подробнее.")
        return

    data = await state.get_data()
    payload = (
        f"<b>Имя персонажа:</b> {escape(data['name'])}\n"
        f"<b>Возраст:</b> {escape(data['age'])}\n"
        f"<b>Игровой ник:</b> {escape(data['nickname'])}\n"
        f"<b>Опыт RP:</b> {escape(data['experience'])}\n"
        f"<b>Причина вступления:</b> {escape(text)}"
    )

    request_id = await send_to_staff(
        bot,
        message,
        "academy",
        "🎓 ЗАЯВКА В АКАДЕМИЮ APD",
        payload,
    )
    await state.clear()
    await message.answer(
        f"✅ Заявка <b>APD-{request_id:06d}</b> зарегистрирована.",
        reply_markup=MENU,
    )


# ---------------- ЖАЛОБА ----------------

@router.message(F.text == "🚨 Жалоба")
async def complaint_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.complaint_employee)
    await message.answer(
        "Укажите имя, позывной или ник сотрудника:",
        reply_markup=CANCEL,
    )


@router.message(Form.complaint_employee)
async def complaint_employee(message: Message, state: FSMContext) -> None:
    await state.update_data(employee=(message.text or "").strip())
    await state.set_state(Form.complaint_description)
    await message.answer("Подробно опишите нарушение:")


@router.message(Form.complaint_description)
async def complaint_description(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("Описание слишком короткое.")
        return
    await state.update_data(description=text)
    await state.set_state(Form.complaint_evidence)
    await message.answer(
        "Отправьте фото, видео или документ.\n"
        "Если доказательств нет, напишите: <b>Нет</b>."
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
        await message.answer("Отправьте файл или напишите «Нет».")
        return

    evidence = "прикреплены" if file_id else escape(message.text or "Нет")
    payload = (
        f"<b>Сотрудник:</b> {escape(data['employee'])}\n"
        f"<b>Описание:</b> {escape(data['description'])}\n"
        f"<b>Доказательства:</b> {evidence}"
    )

    request_id = await send_to_staff(
        bot,
        message,
        "complaint",
        "🚨 ЖАЛОБА НА СОТРУДНИКА APD",
        payload,
        file_id,
        file_type,
    )
    await state.clear()
    await message.answer(
        f"✅ Жалоба <b>APD-{request_id:06d}</b> зарегистрирована.",
        reply_markup=MENU,
    )


# ---------------- БЛАГОДАРНОСТЬ ----------------

@router.message(F.text == "⭐ Благодарность")
async def thanks_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.thanks_employee)
    await message.answer(
        "Укажите имя, позывной или ник сотрудника:",
        reply_markup=CANCEL,
    )


@router.message(Form.thanks_employee)
async def thanks_employee(message: Message, state: FSMContext) -> None:
    await state.update_data(employee=(message.text or "").strip())
    await state.set_state(Form.thanks_text)
    await message.answer("Напишите текст благодарности:")


@router.message(Form.thanks_text)
async def thanks_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Благодарность слишком короткая.")
        return

    data = await state.get_data()
    payload = (
        f"<b>Сотрудник:</b> {escape(data['employee'])}\n"
        f"<b>Благодарность:</b> {escape(text)}"
    )

    request_id = await send_to_staff(
        bot,
        message,
        "thanks",
        "⭐ БЛАГОДАРНОСТЬ СОТРУДНИКУ APD",
        payload,
    )
    await state.clear()
    await message.answer(
        f"✅ Благодарность <b>APD-{request_id:06d}</b> отправлена.",
        reply_markup=MENU,
    )


# ---------------- BACKHOME ----------------

@router.message(F.text == "🏠 BACKHOME")
async def backhome_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.backhome_nickname)
    await message.answer(
        "<b>ПРОГРАММА BACKHOME</b>\n\n"
        "Укажите игровой ник во время службы:",
        reply_markup=CANCEL,
    )


@router.message(Form.backhome_nickname)
async def backhome_nickname(message: Message, state: FSMContext) -> None:
    await state.update_data(nickname=(message.text or "").strip())
    await state.set_state(Form.backhome_rank)
    await message.answer("Укажите прежнее звание или должность:")


@router.message(Form.backhome_rank)
async def backhome_rank(message: Message, state: FSMContext) -> None:
    await state.update_data(rank=(message.text or "").strip())
    await state.set_state(Form.backhome_reason)
    await message.answer("Укажите причину ухода:")


@router.message(Form.backhome_reason)
async def backhome_reason(message: Message, state: FSMContext) -> None:
    await state.update_data(reason=(message.text or "").strip())
    await state.set_state(Form.backhome_evidence)
    await message.answer(
        "Отправьте фото или документ, подтверждающий прошлую службу."
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
        "🏠 ЗАЯВКА BACKHOME",
        payload,
        file_id,
        file_type,
    )
    await state.clear()
    await message.answer(
        f"✅ Заявка <b>APD-{request_id:06d}</b> зарегистрирована.",
        reply_markup=MENU,
    )


# ---------------- ВОПРОСЫ ----------------

@router.message(F.text == "❓ Вопросы")
async def question_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Form.question_text)
    await message.answer(
        "Напишите ваш вопрос руководству APD:",
        reply_markup=CANCEL,
    )


@router.message(Form.question_text)
async def question_finish(message: Message, state: FSMContext, bot: Bot) -> None:
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("Вопрос слишком короткий.")
        return

    payload = f"<b>Вопрос:</b> {escape(text)}"
    request_id = await send_to_staff(
        bot,
        message,
        "question",
        "❓ ВОПРОС В APD",
        payload,
    )
    await state.clear()
    await message.answer(
        f"✅ Вопрос <b>APD-{request_id:06d}</b> отправлен руководству.",
        reply_markup=MENU,
    )


# ---------------- РЕШЕНИЯ ПО ЗАЯВКАМ ----------------

@router.callback_query(F.data.startswith("request:"))
async def request_decision(callback: CallbackQuery, bot: Bot) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет доступа.", show_alert=True)
        return

    _, action, request_id_raw = callback.data.split(":")
    request_id = int(request_id_raw)
    row = get_request(request_id)

    if not row:
        await callback.answer("Обращение не найдено.", show_alert=True)
        return

    statuses = {
        "accept": ("Принято", "✅ Ваше обращение принято."),
        "reject": ("Отклонено", "❌ Ваше обращение отклонено."),
        "review": ("На рассмотрении", "🕓 Ваше обращение находится на рассмотрении."),
    }
    status, user_text = statuses[action]
    update_status(request_id, status)

    prefix = await get_chat_prefix(bot, callback.from_user.id)

    try:
        await bot.send_message(
            row["user_id"],
            f"{user_text}\n"
            f"Номер: <b>APD-{request_id:06d}</b>\n"
            f"Решение вынес: <b>{escape(prefix)}</b>",
        )
    except Exception:
        logging.exception("Не удалось уведомить пользователя")

    base = callback.message.html_text or callback.message.caption or ""
    if "<b>Статус:</b>" in base:
        base = base.rsplit("<b>Статус:</b>", 1)[0].rstrip()

    updated = (
        f"{base}\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Решение вынес:</b> {escape(prefix)}"
    )

    if callback.message.text:
        await callback.message.edit_text(updated)
    else:
        await callback.message.edit_caption(caption=updated)

    await callback.answer(f"Статус: {status}")


# ---------------- ОТВЕТЫ НА ВОПРОСЫ ----------------

@router.callback_query(F.data.startswith("question:"))
async def question_action(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("У вас нет доступа.", show_alert=True)
        return

    _, action, request_id_raw = callback.data.split(":")
    request_id = int(request_id_raw)
    row = get_request(request_id)

    if not row:
        await callback.answer("Вопрос не найден.", show_alert=True)
        return

    prefix = await get_chat_prefix(bot, callback.from_user.id)

    if action == "reply":
        await state.set_state(AdminReply.waiting_text)
        await state.update_data(
            request_id=request_id,
            source_message_id=callback.message.message_id,
            source_chat_id=callback.message.chat.id,
            prefix=prefix,
        )

        await callback.message.reply(
            f"💬 <b>Ответ на вопрос APD-{request_id:06d}</b>\n\n"
            f"<b>Ответ будет отправлен от имени:</b> {escape(prefix)}\n\n"
            "Напишите ответ следующим сообщением в этом чате.\n"
            "Для отмены используйте /cancel"
        )
        await callback.answer("Теперь напишите ответ сообщением.")
        return

    if action == "close":
        update_status(request_id, "Закрыто без ответа")

        base = callback.message.html_text or callback.message.caption or ""
        if "<b>Статус:</b>" in base:
            base = base.rsplit("<b>Статус:</b>", 1)[0].rstrip()

        updated = (
            f"{base}\n"
            f"<b>Статус:</b> Закрыто без ответа\n"
            f"<b>Закрыл:</b> {escape(prefix)}"
        )

        if callback.message.text:
            await callback.message.edit_text(updated)
        else:
            await callback.message.edit_caption(caption=updated)

        try:
            await bot.send_message(
                row["user_id"],
                f"🔒 Ваш вопрос <b>APD-{request_id:06d}</b> закрыт.\n"
                f"Закрыл: <b>{escape(prefix)}</b>",
            )
        except Exception:
            logging.exception("Не удалось уведомить пользователя")

        await callback.answer("Вопрос закрыт.")


@router.message(AdminReply.waiting_text)
async def admin_reply_send(
    message: Message,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not is_admin(message.from_user.id):
        await state.clear()
        return

    if message.chat.id != STAFF_CHAT_ID:
        await message.answer("Ответ нужно отправить в закрытом чате руководства.")
        return

    answer = (message.text or "").strip()
    if len(answer) < 2:
        await message.answer("Ответ слишком короткий.")
        return

    data = await state.get_data()
    request_id = int(data["request_id"])
    prefix = str(data["prefix"])
    row = get_request(request_id)

    if not row:
        await state.clear()
        await message.answer("Обращение не найдено.")
        return

    answered_at = save_answer(
        request_id=request_id,
        answer=answer,
        prefix=prefix,
    )

    try:
        await bot.send_message(
            row["user_id"],
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<b>ОТВЕТ ASTRONOM POLICE DEPARTMENT</b>\n"
            f"<b>Обращение:</b> APD-{request_id:06d}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{escape(answer)}\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>С уважением,</b>\n"
            f"<b>{escape(prefix)}</b>\n"
            "<i>Astronom Police Department</i>"
        )
    except Exception:
        logging.exception("Не удалось отправить ответ пользователю")
        await message.answer("Не удалось отправить ответ пользователю.")
        await state.clear()
        return

    source_chat_id = int(data["source_chat_id"])
    source_message_id = int(data["source_message_id"])

    source_text = (
        format_header("❓ ВОПРОС В APD", request_id)
        + f"<b>Отправитель:</b> {escape(row['full_name'])}\n"
        + f"<b>Telegram:</b> {escape(row['username'] or 'не указан')}\n"
        + f"<b>ID:</b> <code>{row['user_id']}</code>\n\n"
        + row["payload"]
        + "\n\n━━━━━━━━━━━━━━━━━━━━\n"
        + "<b>Статус:</b> Закрыто\n"
        + f"<b>Ответил:</b> {escape(prefix)}\n"
        + f"<b>Время ответа:</b> {escape(answered_at)}\n"
        + f"<b>Ответ:</b> {escape(answer)}"
    )

    try:
        await bot.edit_message_text(
            chat_id=source_chat_id,
            message_id=source_message_id,
            text=source_text,
        )
    except Exception:
        logging.exception("Не удалось обновить карточку вопроса")

    await message.answer(
        f"✅ Ответ по обращению <b>APD-{request_id:06d}</b> отправлен.\n"
        f"<b>Префикс:</b> {escape(prefix)}"
    )
    await state.clear()


@router.message(Command("panel"))
async def panel(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return

    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE status = 'На рассмотрении'"
        ).fetchone()[0]
        closed = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE status LIKE 'Закрыто%'"
        ).fetchone()[0]

    prefix = await get_chat_prefix(bot, message.from_user.id)

    await message.answer(
        "<b>ПАНЕЛЬ РУКОВОДСТВА APD</b>\n\n"
        f"<b>Ваш префикс из чата:</b> {escape(prefix)}\n\n"
        f"📁 Всего обращений: <b>{total}</b>\n"
        f"🕓 На рассмотрении: <b>{pending}</b>\n"
        f"🔒 Закрыто: <b>{closed}</b>"
    )


@router.message(Command("myrole"))
async def my_role(message: Message, bot: Bot) -> None:
    if not is_admin(message.from_user.id):
        return

    prefix = await get_chat_prefix(bot, message.from_user.id)
    await message.answer(
        f"Ваш текущий префикс из чата: <b>{escape(prefix)}</b>"
    )


@router.message()
async def unknown(message: Message) -> None:
    await message.answer(
        "Выберите нужный раздел в меню.",
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
