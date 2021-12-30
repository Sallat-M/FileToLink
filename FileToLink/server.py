from asyncio import get_event_loop
from urllib.parse import unquote
import os

from pyrogram.errors import MessageIdInvalid
from quart import Quart, abort, request, send_file, redirect
from quart.wrappers.response import FileBody as Fb

from FileToLink import Config
from FileToLink.worker import Worker, create_worker, AllWorkers, NotFound


loop = get_event_loop()

app = Quart("FileToLink-Bot")


class FileBody(Fb):
    def __init__(self, file_path, *, buffer_size=None):
        super(FileBody, self).__init__(file_path, buffer_size=buffer_size)
        self.worker: Worker = AllWorkers.get(file_id=str(self.file_path.resolve()).split('/')[-2])
        self.current_part: int = 0
        self.last_read_byte: int = 0

    async def __anext__(self) -> bytes:
        current = await self.file.tell()
        if current >= self.end:
            raise StopAsyncIteration()
        read_size = min(self.buffer_size, self.end - current)
        if (not self.worker.done and current >= self.current_part * Config.Part_size) or current < self.last_read_byte:
            self.current_part = await self.check_dl(current) + 1
            self.last_read_byte = current
        chunk = await self.file.read(read_size)
        if chunk:
            return chunk
        else:
            raise StopAsyncIteration()

    async def check_dl(self, current_byte):
        part_number = self.worker.part_number(current_byte + 1)
        task1, task2 = None, None
        if not self.worker.parts[part_number]:
            task1 = loop.create_task(self.worker.dl(part_number))

        if len(self.worker.parts) > part_number + 1:
            task2 = loop.create_task(self.worker.dl(part_number + 1))

        loop.create_task(self.worker.pre_dl(part_number))

        for task in (task1, task2):
            if task is not None:
                await task

        return part_number


app.response_class.file_body_class = FileBody


@app.route('/')
async def root():
    return redirect(f"https://t.me/{Config.Bot_UserName}")


@app.route('/dl/<int:archive_id>/<name>')
async def download(archive_id: int, name: str):
    worker: Worker = AllWorkers.get(archive_id=archive_id)
    if worker is None:
        try:
            worker: Worker = await create_worker(archive_id)
        except (ValueError, MessageIdInvalid):
            # This Message not found in Archive Channel
            NotFound.append(archive_id)
            return abort(404)  # Not Found

    name = unquote(name)
    if name != worker.name or not os.path.isfile(worker.path):
        abort(404)  # Not Found

    response = await send_file(worker.path, mimetype=worker.mime_type,
                               as_attachment=not bool(request.args.get('st')),
                               attachment_filename=worker.name)
    try:
        if request.range is not None and len(request.range.ranges) > 0:
            await response.make_conditional(request.range, Config.Part_size if not worker.done else None)
    except AssertionError:
        pass  # Bad Range Provided
    response.timeout = None
    return response
