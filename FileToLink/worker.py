from asyncio import sleep
from pathlib import Path
from urllib.parse import quote
import aiofiles
import os

from pyrogram import filters
from pyrogram.errors import MessageDeleteForbidden, MessageIdInvalid
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from FileToLink import bot, Config, Strings


class Worker:
    def __init__(self, msg: Message):
        """
        :param msg: Message from Archive Channel
        """
        if msg.empty:
            raise ValueError
        self.msg = msg
        self.archive_id = msg.message_id
        self.media = (msg.video or msg.document or msg.photo or msg.audio or
                      msg.voice or msg.video_note or msg.sticker or msg.animation)
        self.size = self.media.file_size
        self.id = self.media.file_unique_id
        self.current_dl: int = 0  # Number of currently downloading parts

        if hasattr(self.media, 'mime_type') and self.media.mime_type not in (None, ''):
            self.mime_type = self.media.mime_type
            extension = self.mime_type.split('/')[1] if self.mime_type != 'application/octet-stream' else None
        elif self.msg.photo:
            self.mime_type = 'image/jpeg'
            extension = 'jpg'
        else:
            self.mime_type, extension = None, None

        if hasattr(self.media, 'file_name') and self.media.file_name is not None:
            self.name = self.media.file_name.split('\n')[0].replace("/", "|")
            if self.name.find('.') == -1 and extension:
                self.name += f'.{extension}'
        elif self.msg.photo:
            self.name = f'{self.id}.{extension}'
        else:
            self.name = self.id + (f".{extension}" if extension else '')

        self.link = f'{Config.Link_Root}dl/{self.archive_id}/{quote(self.name)}'

        if self.mime_type:
            self.stream = (bool(self.mime_type.split('/')[0] in ('video', 'audio')) or
                           bool(self.mime_type.split('/')[1] in ('pdf', 'jpg', 'jpeg', 'png')))
        else:
            self.stream = False

        if not os.path.isdir(f"{Config.Download_Folder}/{self.id}"):
            Path(f"{Config.Download_Folder}/{self.id}").mkdir(parents=True, exist_ok=True)
        self.path = f'{Config.Download_Folder}/{self.id}/{self.name}'

        self.parts = [False for _ in
                      range(int(self.size / Config.Part_size) + (1 if self.size % Config.Part_size else 0))]
        self.done = False  # If All parts are downloaded
        self.fast = False  # If User update to Fast Link
        AllWorkers.add(self)

    async def create_file(self):
        """
        Create empty file with same size of real file
        """
        if not os.path.isfile(self.path):
            async with aiofiles.open(self.path, 'wb') as f:
                await f.seek((self.size - 1) if self.size != 0 else 0)
                await f.write(b'\0')

    async def dl(self, part_number: int, one=True):
        """
        :param part_number: Number of part
        :param one: Do not Download more than one part at the same time
        """
        if self.parts[part_number]:
            return

        while one and self.current_dl >= 1:
            await sleep(.05)

        if self.parts[part_number]:
            return

        rng = self.part_range(part_number)
        start = rng.start
        stop = rng.stop

        while not bot.is_connected:
            await sleep(.2)

        self.current_dl += 1
        try:
            await bot.download_part(self.msg, self.path, start, stop)
        except Exception as e:
            print(e)
        else:
            self.parts[part_number] = True
            if all(self.parts):
                self.done = True
        finally:
            self.current_dl -= 1

    async def pre_dl(self, current_part, parts_number=Config.Pre_Dl):
        next_part = current_part + 1
        if next_part >= len(self.parts):
            return
        rng = range(next_part, min(next_part + parts_number, len(self.parts) - 1))
        for i in rng:
            if not self.parts[i]:
                await self.dl(i)

    async def first_dl(self):
        """
        Download first part and create task to download the second
        """
        task = bot.loop.create_task(self.dl(0))
        if len(self.parts) > 1:
            bot.loop.create_task(self.dl(1))
        await task

    async def dl_all(self):
        """
        Download all parts of the file
        """
        for i in range(len(self.parts)):
            await self.dl(i)

    def part_range(self, part_number):
        """
        :return: Bytes range of this part
        """
        if part_number > len(self.parts) - 1:
            raise ValueError(f"Max part_number is {len(self.parts) - 1}")
        start = (part_number * Config.Part_size) + 1
        if part_number < len(self.parts) - 1:
            stop = start + Config.Part_size
        else:
            stop = self.size + 1
        return range(start, stop)

    def part_number(self, byte_number: int):
        """
        :return: Part number that contain this byte
        """
        if byte_number <= 0:
            raise ValueError("byte_number should be positive")
        elif byte_number > self.size:
            raise ValueError("byte_number larger than file size")
        return int((byte_number - 1) / Config.Part_size)


class Workers:
    def __init__(self):
        """ Store Workers by file_unique_id and by archive_id """
        self.by_file_id = {}
        self.by_archive_id = {}

    def get(self, archive_id: int = None, file_id: str = None):
        """Get the worker by archive_id or by file_id"""
        if archive_id is not None and archive_id in self.by_archive_id:
            return self.by_archive_id[archive_id]
        elif file_id is not None and file_id in self.by_file_id:
            return self.by_file_id[file_id]
        else:
            return None

    def add(self, worker):
        """Add the worker to <self.by_file_id> and <self.by_archive_id>"""
        if worker.id not in self.by_file_id:
            self.by_file_id[worker.id] = worker
        if worker.msg.message_id not in self.by_archive_id:
            self.by_archive_id[worker.archive_id] = worker

    def remove(self, archive_id: int):
        """Remove the worker from <self.by_file_id> and <self.by_archive_id>"""
        if archive_id in self.by_archive_id:
            file_id = self.by_archive_id[archive_id].id
            if file_id in self.by_file_id:
                del self.by_file_id[file_id]
            del self.by_archive_id[archive_id]


AllWorkers = Workers()
NotFound = []  # Store IDs of messages that not exist in Archive Channel
FastProcesses = {}  # {User_ID: Number_Of_Link_Updating}


async def create_worker(archive_msg_id):
    if archive_msg_id in NotFound:
        raise ValueError
    msg: Message = await bot.get_messages(Config.Archive_Channel_ID, archive_msg_id)
    if msg.empty or not msg.media:
        raise ValueError
    worker = Worker(msg)
    AllWorkers.add(worker)
    await worker.create_file()
    return worker


@bot.on_callback_query(filters.create(lambda _, __, cb: cb.data.split('|')[0] == 'fast'))
async def update_to_fast_link(_, cb: CallbackQuery):
    msg = cb.message
    user_id = msg.chat.id
    if user_id in FastProcesses and FastProcesses[user_id] >= Config.Max_Fast_Processes:
        await cb.answer(Strings.update_limited, show_alert=True)
        return

    archive_id = int(cb.data.split('|')[1])
    worker: Worker = AllWorkers.get(archive_id=archive_id)
    if worker is None:
        try:
            worker = await create_worker(archive_id)
        except (ValueError, MessageIdInvalid):
            await cb.answer(Strings.file_not_found, show_alert=True)
            return
    if worker.fast:
        await cb.answer(Strings.already_updated, show_alert=True)
        return

    buttons = cb.message.reply_markup.inline_keyboard
    update_button_row = -1  # Last Row
    old_data = buttons[update_button_row][0].callback_data
    buttons[update_button_row][0].text = Strings.wait_update
    new_data = f"fast-prog|{archive_id}"
    buttons[update_button_row][0].callback_data = new_data

    await cb.answer(Strings.wait)
    await cb.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
    progress = await msg.reply_text(
        Strings.wait_update, reply_to_message_id=msg.message_id,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(Strings.progress, callback_data=new_data)]]))

    if user_id in FastProcesses:
        FastProcesses[user_id] += 1
    else:
        FastProcesses[user_id] = 1

    await worker.dl_all()
    worker.fast = True

    FastProcesses[user_id] -= 1

    buttons[update_button_row][0].text = Strings.re_update_link
    buttons[update_button_row][0].callback_data = old_data
    await msg.edit_reply_markup(InlineKeyboardMarkup(buttons))
    await progress.edit_text(Strings.fast)


@bot.on_callback_query(filters.create(lambda _, __, cb: cb.data.split('|')[0] == 'fast-prog'))
async def fast_progress(_, cb: CallbackQuery):
    archive_id = int(cb.data.split('|')[1])
    worker = AllWorkers.get(archive_id=archive_id)
    if worker is None:
        await cb.answer(Strings.file_not_found, show_alert=True)
        return
    downloaded = len([i for i in worker.parts if i])
    total = len(worker.parts)
    await cb.answer(progress_bar(downloaded, total), show_alert=True)


@bot.on_callback_query(filters.create(lambda _, __, cb: cb.data == 'delete-file'))
async def delete_file_handler(_, cb: CallbackQuery):
    msg = cb.message
    AllWorkers.remove(msg.message_id)
    try:
        await msg.delete()
    except MessageDeleteForbidden:
        button = InlineKeyboardButton(Strings.delete_manually_button, callback_data='time-out')
        await msg.edit_reply_markup(InlineKeyboardMarkup([[button]]))


def progress_bar(current, total, length=16, finished='█', unfinished='░'):
    rate = current / total
    finished_len = int(length * rate) if rate <= 1 else length
    return f'{finished * finished_len}{unfinished * (length - finished_len)} {int(rate * 100)}%'
