""" Userge Voice-Call Plugin """

# Copyright (C) 2020-2021 by UsergeTeam@Github, < https://github.com/UsergeTeam >.
#
# This file is part of < https://github.com/UsergeTeam/Userge > project,
# and is released under the "GNU v3.0 License Agreement".
# Please see < https://github.com/UsergeTeam/Userge/blob/master/LICENSE >
#
# All rights reserved
#
# Author (C) - @Krishna_Singhal (https://github.com/Krishna-Singhal)

import asyncio
import glob
import os
import re
import shutil
from traceback import format_exc
from typing import List, Optional, Tuple

import ffmpeg
import youtube_dl as ytdl
from pyrogram.errors import MessageDeleteForbidden
from pyrogram.raw import functions
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.types import Message as RawMessage
from pyrogram.types.messages_and_media.message import Str
from pytgcalls import GroupCall
from youtubesearchpython import VideosSearch

from userge import Message, filters, pool, userge
from userge.utils import time_formatter

CHANNEL = userge.getCLogger(__name__)

ADMINS = {}

PLAYING = False

CHAT_NAME = ""
CHAT_ID = 0
QUEUE: List[Message] = []

BACK_BUTTON_TEXT = ""
CQ_MSG: Optional[RawMessage] = None

call = GroupCall(userge, play_on_repeat=False)

yt_regex = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube|youtu|youtube-nocookie)\.(com|be)/"
    r"(watch\?v=|embed/|v/|.+\?v=)?([^&=%?]{11})"
)
_SCHEDULED = "[{title}]({link}) Scheduled to QUEUE on #{position} position"


def _get_scheduled_text(title: str, link: str) -> str:
    return _SCHEDULED.format(title=title, link=link, position=len(QUEUE) + 1)


def vc_chat(func):
    """decorator for Voice-Call chat"""

    async def checker(msg: Message):
        if CHAT_ID and msg.chat.id == CHAT_ID:
            await func(msg)
        else:
            try:
                await msg.edit(
                    "`Didn't join any Voice-Call...`"
                ) if msg.from_user.is_self else await msg.delete()
            except MessageDeleteForbidden:
                pass

    return checker


def default_markup():
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(text="??? Skip", callback_data="skip"),
                InlineKeyboardButton(text="???? Queue", callback_data="queue"),
            ]
        ]
    )
    return buttons


async def reply_text(msg: Message, text: str, markup=None, to_reply=True) -> Message:
    return await msg.client.send_message(
        msg.chat.id,
        text,
        reply_to_message_id=msg.message_id if to_reply else None,
        reply_markup=markup,
        disable_web_page_preview=True,
    )


async def cache_admins(chat_id: int) -> None:
    k = [
        member.user.id
        async for member in userge.iter_chat_members(chat_id)
        if member.status in ("creator", "administrator")
    ]
    ADMINS[chat_id] = k


@userge.on_cmd(
    "joinvc",
    about={"header": "Join Voice-Call", "usage": "{tr}joinvc"},
    allow_private=False,
    allow_channels=False,
)
async def joinvc(msg: Message):
    """join voice chat"""
    global CHAT_NAME, CHAT_ID  # pylint: disable=global-statement

    await msg.delete()

    if CHAT_NAME:
        await reply_text(msg, f"`Already joined in {CHAT_NAME}`")
        return

    CHAT_ID = msg.chat.id
    CHAT_NAME = msg.chat.title
    try:
        await call.start(CHAT_ID)
    except RuntimeError:
        try:
            peer = await msg.client.resolve_peer(CHAT_ID)
            await msg.client.send(
                functions.phone.CreateGroupCall(peer=peer, random_id=2)
            )
            await asyncio.sleep(3)
            await call.start(CHAT_ID)
        except Exception as err:
            await msg.err(str(err))
            CHAT_ID, CHAT_NAME = 0, ""


@userge.on_cmd(
    "leavevc",
    about={"header": "Leave Voice-Call", "usage": "{tr}leavevc"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def leavevc(msg: Message):
    """leave voice chat"""
    global CHAT_NAME, CHAT_ID  # pylint: disable=global-statement

    await msg.delete()

    if CHAT_NAME:
        await call.stop()
        await asyncio.sleep(2)
        CHAT_NAME = ""
        CHAT_ID = 0
    else:
        await reply_text(msg, "`I didn't find any Voice-Chat to leave")


@userge.on_cmd(
    "play",
    about={"header": "play or add songs to queue"},
    trigger="/",
    filter_me=False,
    allow_private=False,
    allow_bots=False,
    allow_channels=False,
    check_client=True,
)
@vc_chat
async def play_music(msg: Message):
    """play music in voice chat"""
    if not CHAT_ID or msg.chat.id != CHAT_ID:
        return

    if msg.input_str:
        if yt_regex.match(msg.input_str):
            if PLAYING:
                msg = await reply_text(msg, _get_scheduled_text("Song", msg.input_str))
            QUEUE.append(msg)
        else:
            mesg = await reply_text(msg, f"Searching `{msg.input_str}` on YouTube")
            title, link = await _get_song(msg.input_str)
            if link:
                await mesg.delete()
                if PLAYING:
                    msg = await reply_text(msg, _get_scheduled_text(title, link))
                else:
                    msg.text = f"[{title}]({link})"
                QUEUE.append(msg)
            else:
                await mesg.edit("No results found.")
    elif msg.reply_to_message and msg.reply_to_message.audio:
        replied = msg.reply_to_message
        QUEUE.append(replied)
        if PLAYING:
            await reply_text(
                msg, _get_scheduled_text(replied.audio.title, replied.link)
            )
    else:
        return await reply_text(msg, "Input not found")

    if not PLAYING:
        await handle_queue()


@userge.on_cmd(
    "queue",
    about={"header": "View Queue of Songs", "usage": "{tr}queue"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def view_queue(msg: Message):
    """View Queue"""
    await msg.delete()

    if not QUEUE:
        out = "`Queue is empty`"
    else:
        out = f"**{len(QUEUE)} Songs in Queue:**\n"
        for m in QUEUE:
            if m.audio:
                out += f"\n - [{m.audio.title}]({m.link})"
            else:
                title, link = _get_yt_info(m)
                out += f"\n - [{title}]({link})"

    await reply_text(msg, out)


@userge.on_cmd(
    "skip",
    about={"header": "Skip Song", "usage": "{tr}skip"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def skip_music(msg: Message):
    """skip music in vc"""
    await msg.delete()

    await _skip()
    await reply_text(msg, "`Skipped`")


@userge.on_cmd(
    "pause",
    about={"header": "Pause Song.", "usage": "{tr}pause"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def pause_music(msg: Message):
    """paise music in vc"""
    await msg.delete()

    call.pause_playout()
    await reply_text(msg, "?????? **Paused** Music Successfully")


@userge.on_cmd(
    "resume",
    about={"header": "Resume Song.", "usage": "{tr}resume"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def resume_music(msg: Message):
    """resume music in vc"""
    await msg.delete()

    call.resume_playout()
    await reply_text(msg, "?????? **Resumed** Music Successfully")


@userge.on_cmd(
    "stopvc",
    about={"header": "Stop vc and clear Queue.", "usage": "{tr}stopvc"},
    allow_private=False,
    allow_channels=False,
)
@vc_chat
async def stop_music(msg: Message):
    """stop music in vc"""
    await msg.delete()
    await _skip(True)

    await reply_text(msg, "`Stopped Userge-Music.`")


@call.on_network_status_changed
async def nsc_handler(c: GroupCall, connected: bool):
    global PLAYING  # pylint: disable=global-statement

    PLAYING = False

    if os.path.exists("output.raw"):
        os.remove("output.raw")

    await userge.send_message(
        int("-100" + str(c.full_chat.id)) if connected else CHAT_ID,
        f"`{'Joined' if connected else 'Left'} Voice-Chat Successfully`",
    )


@call.on_playout_ended
async def skip_handler(_, __):
    await handle_queue()


async def handle_queue():
    global PLAYING  # pylint: disable=global-statement

    PLAYING = True
    await _skip()


async def _skip(clear_queue: bool = False):
    global PLAYING, CQ_MSG  # pylint: disable=global-statement

    call.input_filename = ""

    if CQ_MSG:
        await CQ_MSG.delete()

    if clear_queue:
        QUEUE.clear()

    if not QUEUE:
        PLAYING = False
        return

    shutil.rmtree("temp_music_dir", ignore_errors=True)
    msg = QUEUE.pop(0)
    try:
        if msg.audio:
            await tg_down(msg)
        else:
            await yt_down(msg)
    except Exception as err:
        PLAYING = False
        out = f"**ERROR:** `{str(err)}`"
        await CHANNEL.log(f"`{format_exc().strip()}`")
        if QUEUE:
            out += "\n\n`Playing next Song.`"
        await userge.send_message(CHAT_ID, out, disable_web_page_preview=True)
        await handle_queue()
    finally:
        shutil.rmtree("temp_music_dir", ignore_errors=True)


async def yt_down(msg: Message):
    global BACK_BUTTON_TEXT, CQ_MSG  # pylint: disable=global-statement

    url = _get_yt_link(msg)
    message = await reply_text(msg, "`Downloading this Song...`")
    title, duration = await mp3_down(url.strip())

    audio_path = None
    for i in ["*.mp3", "*.flac", "*.wav", "*.m4a"]:
        aup = glob.glob("temp_music_dir/" + i)
        if aup and aup[0] and os.path.exists(aup[0]):
            audio_path = aup[0]
            break

    if audio_path is None or not os.path.exists(audio_path):
        raise Exception("Song not Downloaded, add again in Queue [your wish]")

    await message.edit("`Transcoding...`")
    call.input_filename = await _transcode(audio_path)
    await message.delete()

    def requester():
        replied = msg.reply_to_message
        if replied and msg.client.id == msg.from_user.id:
            return replied.from_user.mention
        return msg.from_user.mention

    BACK_BUTTON_TEXT = (
        f"???? **Now playing:** [{title}]({url})\n"
        f"??? **Duration:** `{duration}`\n"
        f"???? **Requested By:** {requester()}"
    )

    CQ_MSG = await reply_text(
        msg,
        BACK_BUTTON_TEXT,
        markup=default_markup() if userge.has_bot else None,
        to_reply=False,
    )

    if msg.client.id == msg.from_user.id:
        await msg.delete()


async def tg_down(msg: Message):
    global BACK_BUTTON_TEXT, CQ_MSG  # pylint: disable=global-statement

    message = await reply_text(msg, "`Downloading this Song...`")
    path = await msg.download("temp_music_dir/")
    filename = os.path.join("temp_music_dir", os.path.basename(path))

    await message.edit("`Transcoding...`")
    call.input_filename = await _transcode(filename)
    await message.delete()

    BACK_BUTTON_TEXT = (
        f"???? **Now playing:** [{msg.audio.title}]({msg.link})\n"
        f"??? **Duration:** `{time_formatter(msg.audio.duration)}`\n"
        f"???? **Requested By:** {msg.from_user.mention}"
    )

    CQ_MSG = await reply_text(
        msg,
        BACK_BUTTON_TEXT,
        markup=default_markup() if userge.has_bot else None,
        to_reply=False,
    )


def _get_yt_link(msg: Message) -> str:
    text = msg.text
    if isinstance(text, Str):
        text = text.markdown
    for _ in yt_regex.finditer(text):
        return _.group(0)
    return ""


def _get_yt_info(msg: Message) -> Tuple[str, str]:
    for e in msg.entities:
        if e.url:
            return msg.text[e.offset : e.length], e.url
    return "Song", _get_yt_link(msg)


@pool.run_in_thread
def _get_song(name: str) -> Tuple[str, str]:
    results: List[dict] = VideosSearch(name, limit=1).result()["result"]
    if results:
        return results[0].get("title", name), results[0].get("link")
    return name, ""


@pool.run_in_thread
def mp3_down(url: str):
    ydl_opts = {
        "outtmpl": os.path.join("temp_music_dir", "%(title)s.%(ext)s"),
        "prefer_ffmpeg": True,
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "320",
            },
            {"key": "FFmpegMetadata"},
        ],
    }

    with ytdl.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url)

    return info.get("title"), time_formatter(info.get("duration"))


@pool.run_in_thread
def _transcode(input_: str) -> str:
    output = "output.raw"
    ffmpeg.input(input_).output(
        output, format="s16le", acodec="pcm_s16le", ac=2, ar="48k"
    ).overwrite_output().run()
    return output


if userge.has_bot:

    @userge.bot.on_callback_query(filters.regex("(skip|queue|back)"))
    async def vc_callback(_, cq: CallbackQuery):
        global CQ_MSG  # pylint: disable=global-statement
        if not CHAT_NAME:
            await cq.edit_message_text("`Already Left Voice-Call`")
            return

        if "skip" in cq.data:
            if not ADMINS or not ADMINS.get(cq.message.chat.id):
                await cache_admins(cq.message.chat.id)

            if cq.from_user.id not in ADMINS[cq.message.chat.id]:
                return await cq.answer("Only Admins can Skip Song.")

            text = f"{cq.from_user.mention} Skipped this Song."
            pattern = re.compile(r"\((.*)\)")
            url = None
            for match in pattern.finditer(BACK_BUTTON_TEXT):
                url = match.group(1)
                break
            if url:
                text = f"{cq.from_user.mention} Skipped this [Song]({url})."

            CQ_MSG = None
            await cq.edit_message_text(text, disable_web_page_preview=True)
            await handle_queue()

        elif "queue" in cq.data:
            if not QUEUE:
                out = "`Queue is empty.`"
            else:
                out = f"**{len(QUEUE)} Song"
                out += f"{'s' if len(QUEUE) > 1 else ''} in Queue:**\n"
                for m in QUEUE:
                    if m.audio:
                        out += f"\n - [{m.audio.title}]({m.link})"
                    else:
                        title, link = _get_yt_info(m)
                        out += f"\n - [{title}]({link})"

            out += f"\n\n**Clicked by:** {cq.from_user.mention}"
            button = InlineKeyboardMarkup(
                [[InlineKeyboardButton(text="Back", callback_data="back")]]
            )

            CQ_MSG = None
            await cq.edit_message_text(
                out, disable_web_page_preview=True, reply_markup=button
            )

        elif "back" in cq.data:
            if BACK_BUTTON_TEXT:
                CQ_MSG = cq.message
                await cq.edit_message_text(
                    BACK_BUTTON_TEXT,
                    disable_web_page_preview=True,
                    reply_markup=default_markup(),
                )
            else:
                await cq.message.delete()
