from FileToLink.client import bot
from FileToLink.config import Config, Strings

from pyrogram.errors import ChatAdminRequired, UserNotParticipant
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton


async def participant(user_id: int):
    if Config.Bot_Channel is None:
        return True
    try:
        await bot.get_chat_member(Config.Bot_Channel, user_id)
    except ChatAdminRequired:
        print(f"Please Add the Bot to @{Config.Bot_Channel} as Admin")
        return True
    except UserNotParticipant:
        buttons = [[InlineKeyboardButton(Strings.bot_channel, url=f'https://t.me/{Config.Bot_Channel}')]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await bot.send_message(user_id, Strings.force_join, reply_markup=reply_markup)
        return False
    else:
        return True


def progress_bar(current, total, length=16, finished='█', unfinished='░'):
    rate = current / total
    finished_len = int(length * rate) if rate <= 1 else length
    return f'{finished * finished_len}{unfinished * (length - finished_len)} {int(rate * 100)}%'
