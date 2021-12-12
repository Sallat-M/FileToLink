from pathlib import Path
from asyncio import sleep
import aiofiles
import os

from pyrogram import filters
from pyrogram.errors import MessageDeleteForbidden
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from FileToLink import bot, Config


class Worker:
    def __init__(self, msg: Message):
        if msg.empty:
            raise ValueError
        self.msg = msg
        self.media = (msg.video or msg.document or msg.photo or msg.audio or
                      msg.voice or msg.video_note or msg.sticker or msg.animation)
        self.size = self.media.file_size
        self.id = self.media.file_unique_id
        self.link = None
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
        AllWorkers.add(self)

    def set_link(self, archive_id: int):
        self.link = f'{Config.Link_Root}dl/{archive_id}'

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
        """
        Store Workers by file_unique_id and by archive_id
        """
        self.by_file_id = {}
        self.by_archive_id = {}

    def get(self, archive_id: int = None, file_id: str = None):
        if archive_id is not None and archive_id in self.by_archive_id:
            return self.by_archive_id[archive_id]
        elif file_id is not None and file_id in self.by_file_id:
            return self.by_file_id[file_id]
        else:
            return None

    def add(self, worker, archive_id: int = None):
        if worker.id not in self.by_file_id:
            self.by_file_id[worker.id] = worker
        if archive_id is not None and archive_id not in self.by_archive_id:
            self.by_archive_id[archive_id] = worker

    def remove(self, archive_id: int = None, file_id: str = None):
        if archive_id is not None and archive_id in self.by_archive_id:
            del self.by_file_id[self.by_archive_id[archive_id].id]
            del self.by_archive_id[archive_id]
        elif file_id is not None and file_id in self.by_file_id:
            del self.by_file_id[file_id]


AllWorkers = Workers()
NotFound = []  # Store IDs of messages that not exist in Archive Channel


async def create_worker(archive_msg_id):
    if archive_msg_id in NotFound:
        raise ValueError
    msg: Message = await bot.get_messages(Config.Archive_Channel_ID, archive_msg_id)
    if msg.empty or not msg.media:
        raise ValueError
    worker = Worker(msg)
    AllWorkers.add(worker, archive_id=archive_msg_id)
    await worker.create_file()
    return worker


@bot.on_callback_query(filters.create(lambda _, __, cb: cb.data == 'delete-file'))
async def delete_file_handler(_, cb: CallbackQuery):
    msg = cb.message
    AllWorkers.remove(msg.message_id)
    try:
        await msg.delete()
    except MessageDeleteForbidden:
        button = InlineKeyboardButton("⚠️You can delete it", callback_data='time-out')
        await msg.edit_reply_markup(InlineKeyboardMarkup([[button]]))
