import os
from typing import Union

import aiofiles
from pyrogram import Client, raw, utils, types
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource, PHOTO_TYPES
from pyrogram.session import Auth, Session
from pyrogram.storage import Storage

from FileToLink.config import Config


class TelegramClient(Client):
    def __init__(
            self,
            session_name: Union[str, Storage],
            api_id: Union[int, str] = None,
            api_hash: str = None,
            bot_token: str = None,
            sleep_threshold: int = Session.SLEEP_THRESHOLD
    ):
        super(TelegramClient, self).__init__(session_name, api_id=api_id, api_hash=api_hash,
                                             bot_token=bot_token, sleep_threshold=sleep_threshold)

    async def get_part_file(
            self,
            file_id: FileId,
            file_path: str,
            file_size: int,
            start: int = 0,
            stop: int = None,
            limit: int = 1024 * 1024
    ) -> range:
        dc_id = file_id.dc_id

        async with self.media_sessions_lock:
            session = self.media_sessions.get(dc_id, None)

            if session is None:
                if dc_id != await self.storage.dc_id():
                    session = Session(
                        self, dc_id, await Auth(self, dc_id, await self.storage.test_mode()).create(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await session.start()

                    for _ in range(3):
                        exported_auth = await self.send(
                            raw.functions.auth.ExportAuthorization(
                                dc_id=dc_id
                            )
                        )

                        try:
                            await session.send(
                                raw.functions.auth.ImportAuthorization(
                                    id=exported_auth.id,
                                    bytes=exported_auth.bytes
                                )
                            )
                        except AuthBytesInvalid:
                            continue
                        else:
                            break
                    else:
                        await session.stop()
                        raise AuthBytesInvalid
                else:
                    session = Session(
                        self, dc_id, await self.storage.auth_key(),
                        await self.storage.test_mode(), is_media=True
                    )
                    await session.start()

                self.media_sessions[dc_id] = session

        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id,
                    access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(
                        chat_id=-file_id.chat_id
                    )
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size
            )

        offset = start - 1
        offset = offset - (offset % (4 * 1024))  # Fix offset
        size: int = 0
        if stop is None:
            stop = (file_size if file_size != 0 else 2 * 1024 * 1024 * 1024) + 1
        file_name = ""

        try:
            r = await session.send(
                raw.functions.upload.GetFile(
                    location=location,
                    offset=offset,
                    limit=limit
                ),
                sleep_threshold=30
            )

            if isinstance(r, raw.types.upload.File):
                async with aiofiles.open(file_path, 'rb+') as f:
                    await f.seek(offset)
                    while True:
                        chunk = r.bytes

                        if not chunk:
                            break

                        await f.write(chunk)
                        size += len(chunk)

                        offset += limit

                        if offset + 1 >= stop:
                            break

                        r = await session.send(
                            raw.functions.upload.GetFile(
                                location=location,
                                offset=offset,
                                limit=limit
                            ),
                            sleep_threshold=30
                        )
        except:
            try:
                os.remove(file_name)
            except OSError:
                pass

            return range(start, start + (size + 1 if size != 0 else 0))
        else:
            return range(start, start + size + 1)

    async def download_part(
            self,
            message: Union["types.Message", str],
            file_name: str = "downloads/",
            start: int = 0,
            stop: int = None,
            limit: int = 1024 * 1024
    ) -> range:

        available_media = ("audio", "document", "photo", "sticker", "animation", "video", "voice", "video_note",
                           "new_chat_photo")

        if isinstance(message, types.Message):
            for kind in available_media:
                media = getattr(message, kind, None)

                if media is not None:
                    break
            else:
                raise ValueError("This message doesn't contain any downloadable media")
        else:
            media = message

        if isinstance(media, str):
            file_id_str = media
        else:
            file_id_str = media.file_id

        file_id_obj = FileId.decode(file_id_str)

        file_type = file_id_obj.file_type
        media_file_name = getattr(media, "file_name", "")
        file_size = getattr(media, "file_size", 0)
        mime_type = getattr(media, "mime_type", "")

        directory, file_name = os.path.split(file_name)
        file_name = file_name or media_file_name or ""

        if not os.path.isabs(file_name):
            directory = self.PARENT_DIR / (directory or "downloads/")

        if not file_name:
            guessed_extension = self.guess_extension(mime_type)

            if file_type in PHOTO_TYPES:
                extension = ".jpg"
            elif file_type == FileType.VOICE:
                extension = guessed_extension or ".ogg"
            elif file_type in (FileType.VIDEO, FileType.ANIMATION, FileType.VIDEO_NOTE):
                extension = guessed_extension or ".mp4"
            elif file_type == FileType.DOCUMENT:
                extension = guessed_extension or ".zip"
            elif file_type == FileType.STICKER:
                extension = guessed_extension or ".webp"
            elif file_type == FileType.AUDIO:
                extension = guessed_extension or ".mp3"
            else:
                extension = ".unknown"

            file_name = "{}_{}{}".format(
                FileType(file_id_obj.file_type).name.lower(),
                media.file_unique_id,
                extension
            )

        file_path = os.path.join(directory, file_name)
        if not os.path.isfile(file_path):
            async with aiofiles.open(file_path, 'wb') as f:
                await f.seek((file_size - 1) if file_size != 0 else 0)
                await f.write(b'\0')

        rng = await self.get_part_file(
            file_id=file_id_obj,
            file_path=file_path,
            file_size=file_size,
            start=start,
            stop=stop,
            limit=limit
        )

        return rng


bot = TelegramClient(Config.Session, api_id=Config.API_ID, api_hash=Config.API_HASH,
                     bot_token=Config.Token, sleep_threshold=Config.Sleep_Threshold)
