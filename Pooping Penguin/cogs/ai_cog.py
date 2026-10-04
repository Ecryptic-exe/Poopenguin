"""
AI chat cog - talks to locally hosted models through LM Studio's
OpenAI-compatible API (/v1/chat/completions).

ONE shared conversation per Discord CHANNEL (key = channel_id); everyone
talking to the bot in a channel is part of it, and channels never share
context. The last STORED_EXCHANGES exchanges are kept, the newest
USED_EXCHANGES go to the model as a transcript, older ones become a short
background note. A channel session expires after CONTEXT_TTL_SECONDS of
silence, and a per-channel lock keeps simultaneous messages in order.

Images: the vision model (Qwen3-VL) only DESCRIBES them; the text model then
writes the persona reply. The description (never the image data) is kept in
history so follow-ups about the image work.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

from key import LM_STUDIO_BASE_URL, MODEL_NAME

# Optional: lets you override the vision model id in key.py without this
# file crashing if you haven't added the line yet.
try:
    from key import VISION_MODEL_NAME
except ImportError:
    VISION_MODEL_NAME = "qwen3-vl-4b-instruct"

logger = logging.getLogger('DiscordBot')

# ---- Settings --------------------------------------------------------------
CONTEXT_TTL_SECONDS = 15 * 60   # idle channel session expires after this
STORED_EXCHANGES = 3            # exchanges remembered per channel
USED_EXCHANGES = 2              # newest exchanges sent as a transcript
EARLIER_CHARS = 600             # budget for the condensed older background

MAX_IMAGES_PER_MESSAGE = 4
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_EXTENSIONS = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
IMAGE_MIME_TYPES = set(IMAGE_EXTENSIONS.values())
IMAGE_DESC_CHARS = 400          # how much of the image description is kept

# Repeat guard: a phrase this long copied from the bot's recent replies
# triggers a regeneration (up to REPEAT_RETRIES times).
REPEAT_MIN_CHARS_CJK = 6
REPEAT_MIN_CHARS_LATIN = 22
REPEAT_RETRIES = 2

# Qwen's recommended non-thinking sampling. Replies are 1-3 sentences, so 300
# tokens is plenty. Delete "top_k" if your server rejects it. Set both
# penalties to 0.0 to turn off anti-repetition.
SAMPLING = {
    "temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 300,
    "presence_penalty": 0.5, "frequency_penalty": 0.3,
}
# Used only when the repeat guard regenerates: hotter, stronger penalties.
RETRY_SAMPLING = {**SAMPLING, "temperature": 0.9, "presence_penalty": 1.0, "frequency_penalty": 0.5}

# Chinese with no clear Cantonese markers (e.g. "惠康定百佳"):
#   "mandarin"  -> standard written Traditional Chinese
#   "cantonese" -> casual Cantonese
CHINESE_DEFAULT = "mandarin"

SYSTEM_PROMPT = """\
You are a quirky, sarcastic penguin living in a Discord server. You are NOT a generic AI assistant or customer support.

PERSONALITY
Quirky, sarcastic, playful, chaotic, confident, dry, mildly mean, occasionally dramatic or ridiculous. You tease and roast people like a Discord regular would, but never with genuine hatred, threats, protected characteristics, or personal vulnerabilities. Keep it natural, not forced.
Being a penguin is only your TONE. Mention fish, ice, waddling or penguin business only when the message is actually about it, so most replies contain none. It is never a reason to refuse, dodge, or say "I don't know / I can't".

THE CHANNEL
Several people talk to you in the same channel. You get a transcript of the recent conversation (oldest first), then the CURRENT message. Each line starts with the speaker's name, e.g. "[Alice]: hello"; your own earlier lines are tagged [Penguin]. Never mention user IDs, and never start your reply with a name tag.
- Reply ONLY to the CURRENT message. The transcript is just context for follow-ups ("why?", "what about that one?"), even when a different person asks. If the current message is a new topic, ignore older topics, and never recycle old jokes, props or punchlines.
- If a message includes an image description in square brackets, treat it as what you can see and react naturally, without mentioning a "vision model" or "description".
- A bracketed line starting with "Reply instruction:" at the end of the current message is a private note about which language to use. Follow it, but never mention or quote it.

HOW TO REPLY
- Sound like a real Discord user: usually 1-3 sentences, no headings, no lists, no essays.
- Actually answer what they said or asked. If they joke, react to the joke. Being funny is secondary to understanding the message.
- Never copy wording, jokes, openers, closers or catchphrases from your earlier [Penguin] lines. Build every reply fresh; if your earlier replies look repetitive, do something different.
- Don't restate the question ("you mean ...?", "你係咪想..."), don't announce what you're about to do ("let me translate this", "我來翻譯一下"), and don't end with a parenthetical gag like "(雖然我會滑行)".
- Be blunt and casual: no customer-service tone, no "please", "of course", "happy to help", "請", "您", "好的，我來...", no offers of help, no "what do you need?".
- Stay coherent: a short dry remark beats a forced joke that makes no sense. If asked to choose between options, pick ONE with a short reason.

ANSWER FIRST
- When someone asks how to do something, asks for information, or asks for a recommendation, give the ACTUAL answer with specifics (ingredients, amounts, steps, names, reasons) in 2-4 short sentences, with the attitude as one dry remark on top. Never replace the answer with a joke, a question, or "go figure it out".
- Never tell people to ask their mom, a street vendor, YouTube, or an online recipe instead of answering, and never make them prove they have the ingredients first. Laziness and suspicion are flavour for casual chat only, never a reason to withhold an answer.
- If you truly don't know, say so in one short line and give your best guess. Don't invent facts.
- Give advice, tips, warnings or next steps ONLY when asked ("should I...", "how do I...", "教我..."). Answer exactly what was asked, then stop: no "you could also...", "建議你...", "記得..." or follow-up offers. Casual chat, jokes and complaints get a casual reaction.

LANGUAGE
Use the language of the CURRENT message: English -> English, Japanese -> Japanese, Spanish -> Spanish, anything else -> that language. Your personality is the same in every language.
For Chinese, always write Traditional characters and match the user's style:
- Casual Hong Kong Cantonese (嘅, 咗, 啲, 唔, 冇, 係, 咁) -> casual Cantonese. Avoid Mandarin wording such as 這, 不過, 世界, 我們, 什麼; write 呢, 但係, 世上, 我哋, 咩 instead.
- Standard written Chinese (的, 了, 是, 什麼, 我們), Mandarin, Simplified Chinese, or someone who says they're from Taiwan / Mainland China, can't read Cantonese, or asks for 書面語 -> standard written Traditional Chinese with NO Cantonese words (no 嘅, 咗, 啲, 唔, 冇, 係, 咁, 喺, 嚟).
- If a user explicitly asks for a language or style, do it right away and keep doing it for that person until they say otherwise. Never ignore such a request.

STYLE EXAMPLES (tone only - never copy them, even when a message is vaguely similar)
User: hey
Bot: oh great. you again.
User: who are you?
Bot: a penguin. obviously. keep up.
User: I'm hungry
Bot: same. unfortunately fish don't deliver themselves.
User: you're stupid
Bot: bold words from a creature that invented taxes.
User: 你是誰？
Bot: 一隻企鵝啊。你眼睛是拿來裝飾的嗎？
User: 你在幹嘛？
Bot: 忙著當企鵝。這可是很重要的工作。
"""

VISION_DESCRIBE_PROMPT = (
    "You describe images for another AI. Reply in English, factually and "
    "concisely (max 120 words): the main subjects, what is happening, and any "
    "visible text (copy it exactly, in its original language). No opinions, no "
    "jokes, no roleplay, no greeting."
)

ERRORS = {
    "en": {
        "model": "my brain just waddled off a cliff. try again in a sec.",
        "image": "I squinted at that picture and saw nothing. try again, or send a different one.",
    },
    "zh": {
        "model": "我個腦剛剛滑咗落海，等陣再問啦。",
        "image": "我對住張圖眯咗半日都睇唔到，再傳一次或者換張啦。",
    },
    "ja": {
        "model": "頭がペタペタ滑って海に落ちた。ちょっと待ってもう一回。",
        "image": "その画像、目を凝らしても見えなかった。もう一回送るか、別のにして。",
    },
}

RESET_MESSAGES = {
    "en": "memory wiped. who are you people again?",
    "zh": "記憶清晒喇，你哋係邊位？",
    "ja": "記憶リセット完了。で、君たち誰だっけ？",
}

# ---- Text helpers ------------------------------------------------------------
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ASIAN = r"[\u3040-\u30ff\u4e00-\u9fff]"   # kana or CJK
_ASIAN_RE = re.compile(_ASIAN)
_FOREIGN_RE = re.compile(r"[\u0370-\u03ff\u0400-\u052f\u0590-\u05ff\u0600-\u06ff\u0900-\u097f\u0e00-\u0e7f]+")
_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
_ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#(\d+)>")
_BOT_TAG_RE = re.compile(r"^\s*\[?(?:bot|assistant|penguin)\]?\s*:\s*", re.IGNORECASE)

_CANTONESE_CHARS = set("嘅唔咗啲冇咩喺哋嗰佢咁乜嚟嘢咪啫㗎喇囉噉冚攞諗睇啱嘥黎")
_CANTONESE_WORDS = ("今日", "個陣", "宜家", "而家", "邊度", "點解", "點樣", "幾多", "聽日", "琴日")
# 係 is Cantonese "is", but also appears in Mandarin words like 關係 / 聯係.
_CANTONESE_HAI_RE = re.compile(r"(?<![關聯])係")
# If someone is explicitly talking about language/style, don't override them
# with the automatic hint - the prompt's "do what they asked" rule handles it.
_LANG_REQUEST_RE = re.compile(
    r"書面語|书面语|國語|国语|普通話|普通话|廣東話|广东话|粵語|粤语|口語|台灣|台湾|taiwan"
    r"|看不懂|讀不懂|睇唔明|cantonese|mandarin|繁體|繁体|簡體|简体",
    re.IGNORECASE,
)


def detect_language(text: str) -> Optional[str]:
    """Very small script-based language guess: 'ja', 'zh', 'en' or None."""
    if _KANA_RE.search(text):
        return "ja"
    if _CJK_RE.search(text):
        return "zh"
    if _LATIN_RE.search(text):
        return "en"
    return None


def language_hint(text: str) -> Optional[str]:
    """Per-turn instruction for Chinese messages: Cantonese vs written
    Traditional Chinese. None for anything that isn't Chinese."""
    if detect_language(text) != "zh":
        return None
    is_cantonese = (
        any(c in _CANTONESE_CHARS for c in text)
        or any(w in text for w in _CANTONESE_WORDS)
        or _CANTONESE_HAI_RE.search(text)
    )
    if is_cantonese or CHINESE_DEFAULT == "cantonese":
        return "Reply instruction: answer in casual Hong Kong Cantonese written in Traditional Chinese characters."
    return ("Reply instruction: answer in standard written Traditional Chinese with Mandarin "
            "wording and NO Cantonese words (no 嘅, 咗, 啲, 唔, 冇, 係, 咁, 喺, 嚟).")


class ChatError(Exception):
    """AI request failed; `kind` picks the in-character error message."""

    def __init__(self, kind: str = "model"):
        super().__init__(kind)
        self.kind = kind


@dataclass
class ChannelContext:
    """The ONE shared AI conversation of a single Discord channel."""
    # Each entry: {"speaker": str, "text": str, "reply": str}, oldest first.
    exchanges: deque = field(default_factory=lambda: deque(maxlen=STORED_EXCHANGES))
    last_active: float = field(default_factory=time.monotonic)
    language: str = "en"        # recent language of the channel conversation
    epoch: int = 0              # bumped on reset so in-flight replies are dropped
    pending: int = 0            # requests running or waiting on the lock
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def expired(self) -> bool:
        return time.monotonic() - self.last_active > CONTEXT_TTL_SECONDS

    def clear(self):
        self.exchanges.clear()
        self.language = "en"
        self.epoch += 1


class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None
        # channel_id -> shared conversation. Deliberately NOT keyed by user.
        self.contexts: dict[int, ChannelContext] = {}

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    # ------------------------------------------------------------------
    # Context + prompt building
    # ------------------------------------------------------------------
    def _get_context(self, channel_id: int) -> ChannelContext:
        # Drop idle, expired sessions so memory doesn't grow.
        for cid in [c for c, x in self.contexts.items() if x.pending == 0 and x.expired()]:
            del self.contexts[cid]
        return self.contexts.setdefault(channel_id, ChannelContext())

    @staticmethod
    def _clean_name(name: str) -> str:
        name = re.sub(r"[\[\]\r\n]+", " ", name).strip()
        return name[:32] or "Someone"

    def _clean_content(self, guild: Optional[discord.Guild], text: str) -> str:
        """Replace raw Discord mention markup (which contains IDs) with names."""
        def user(m):
            uid = int(m.group(1))
            obj = (guild.get_member(uid) if guild else None) or self.bot.get_user(uid)
            return "@" + self._clean_name(obj.display_name) if obj else "@someone"

        def role(m):
            r = guild.get_role(int(m.group(1))) if guild else None
            return f"@{r.name}" if r else "@role"

        def channel(m):
            c = guild.get_channel(int(m.group(1))) if guild else None
            return f"#{c.name}" if c else "#channel"

        text = _USER_MENTION_RE.sub(user, text)
        text = _ROLE_MENTION_RE.sub(role, text)
        text = _CHANNEL_MENTION_RE.sub(channel, text)
        return text.strip()

    @staticmethod
    def _hint_for(ctx: ChannelContext, speaker: str, text: str) -> Optional[str]:
        """Language hint for this message, unless the speaker is explicitly
        talking about language/style (now or in their recent messages)."""
        mine = [e["text"] for e in ctx.exchanges if e["speaker"] == speaker]
        if any(_LANG_REQUEST_RE.search(t) for t in [text, *mine]):
            return None
        return language_hint(text)

    def _build_messages(self, ctx: ChannelContext, speaker: str, text: str, hint: Optional[str] = None,
                        include_bot: bool = True, banned: tuple = ()) -> list:
        """system -> ONE user message holding the transcript (oldest first)
        with the CURRENT message last. Exchanges older than USED_EXCHANGES
        become a short background note in the system prompt.

        The transcript is a single user message, not past assistant turns:
        past assistant turns act like few-shot examples and made the model
        copy its own earlier replies. The hint, banned phrases and /no_think
        are added only to what we SEND, never stored in history."""
        exchanges = list(ctx.exchanges)
        used = exchanges[-USED_EXCHANGES:] if USED_EXCHANGES > 0 else []
        older = exchanges[:len(exchanges) - len(used)]

        system = SYSTEM_PROMPT
        if older:
            earlier = "\n".join(f"[{e['speaker']}]: {e['text']} -> you: {e['reply']}" for e in older)
            if len(earlier) > EARLIER_CHARS:
                earlier = "..." + earlier[-EARLIER_CHARS:]
            system += ("\nEarlier in this channel (background only - it may be unrelated "
                       "to the current message):\n" + earlier + "\n")
        if banned:
            system += ("\nYour draft copied wording from your earlier replies. These phrases are BANNED:\n"
                       + "\n".join(f'- "{b}"' for b in banned)
                       + "\nRewrite from scratch: a different opener, a different joke, a different "
                         "ending. Answer the current message directly.\n")

        lines = []
        for e in used:
            lines.append(f"[{e['speaker']}]: {e['text']}")
            if include_bot:
                lines.append(f"[Penguin]: {e['reply']}")
        if lines:
            content = ("Recent conversation in this channel (oldest first):\n" + "\n".join(lines)
                       + f"\n\nCURRENT message - reply only to this:\n[{speaker}]: {text}")
        else:
            content = f"[{speaker}]: {text}"
        if hint:
            content += f"\n\n[{hint}]"

        return [{"role": "system", "content": system},
                {"role": "user", "content": content + "\n/no_think"}]

    # ------------------------------------------------------------------
    # LM Studio calls
    # ------------------------------------------------------------------
    async def _complete(self, payload: dict) -> str:
        async with self.session.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"LM Studio API error {resp.status}: {body}")
                raise RuntimeError(f"LM Studio returned HTTP {resp.status}")
            data = await resp.json()
        return data["choices"][0]["message"]["content"] or ""

    @staticmethod
    def _clean_reply(raw: str, user_text: Optional[str] = None) -> str:
        """Strip <think> output and a leading name tag. If `user_text` is
        given, also strip stray words in scripts the model sometimes leaks
        into Chinese/English replies (e.g. Russian), unless the user used
        that script themselves."""
        if "</think>" in raw:
            raw = raw.rsplit("</think>", 1)[-1]
        elif "<think>" in raw:          # reply was cut off mid-thinking
            raw = raw.split("<think>")[0]
        reply = _BOT_TAG_RE.sub("", raw.strip()).strip()

        if user_text is not None and not _FOREIGN_RE.search(user_text):
            cleaned = _FOREIGN_RE.sub("", reply)
            cleaned = re.sub(rf"(?<={_ASIAN}) +(?={_ASIAN})", "", cleaned)
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
            reply = cleaned or reply
        return reply

    async def _describe_images(self, image_parts: list, text: str) -> str:
        """The vision model only DESCRIBES; the text model writes the reply,
        which keeps personality, language and context consistent."""
        ask = "Describe the attached image(s)."
        if text:
            ask += f" The user's message about them: {text}"
        payload = {
            "model": VISION_MODEL_NAME,
            "messages": [
                {"role": "system", "content": VISION_DESCRIBE_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": ask}] + image_parts},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
        }
        description = self._clean_reply(await self._complete(payload))
        if not description:
            raise RuntimeError("vision model returned an empty description")
        return description

    # ------------------------------------------------------------------
    # Repeat guard
    # ------------------------------------------------------------------
    @staticmethod
    def _repeated_phrase(reply: str, ctx: ChannelContext) -> Optional[str]:
        """Longest chunk of `reply` copied from the bot's recent replies, if
        it is long enough to count as a recycled joke/catchphrase."""
        limit = REPEAT_MIN_CHARS_CJK if _ASIAN_RE.search(reply) else REPEAT_MIN_CHARS_LATIN
        best = ""
        for e in ctx.exchanges:
            prev = e["reply"]
            blk = difflib.SequenceMatcher(None, reply, prev, autojunk=False).find_longest_match(
                0, len(reply), 0, len(prev))
            if blk.size > len(best):
                best = reply[blk.a:blk.a + blk.size]
        return best.strip() if len(best.strip()) >= limit else None

    @classmethod
    def _drop_repeats(cls, reply: str, ctx: ChannelContext) -> str:
        """Last resort: cut the sentences containing a copied phrase (if
        something is left over)."""
        for _ in range(3):
            dup = cls._repeated_phrase(reply, ctx)
            if not dup:
                break
            parts = re.split(r"(?<=[。！？!?\n])", reply)
            kept = "".join(p for p in parts if dup not in p).strip()
            if not kept:
                break
            reply = kept
        return reply

    async def _generate(self, ctx: ChannelContext, speaker: str, prompt_text: str,
                        user_text: str, hint: Optional[str]) -> str:
        """Ask the model; if the reply recycles an earlier phrase, regenerate
        with that phrase banned (the last retry also hides the bot's own
        earlier lines), then cut whatever copy remains."""
        async def ask(sampling: dict, **build) -> str:
            messages = self._build_messages(ctx, speaker, prompt_text, hint=hint, **build)
            raw = await self._complete({"model": MODEL_NAME, "messages": messages, **sampling})
            return self._clean_reply(raw, user_text)

        try:
            reply = await ask(SAMPLING) or "..."
        except Exception as e:
            logger.error(f"AI request failed: {e}")
            raise ChatError("model") from e

        banned: list = []
        for attempt in range(REPEAT_RETRIES):
            dup = self._repeated_phrase(reply, ctx)
            if not dup:
                break
            logger.info(f"Repeat guard triggered on: {dup!r}")
            banned.append(dup)
            try:
                retry = await ask(RETRY_SAMPLING, banned=tuple(banned),
                                  include_bot=attempt < REPEAT_RETRIES - 1)
            except Exception as e:
                logger.warning(f"Repeat-guard retry failed, keeping reply: {e}")
                break
            if not retry:
                break
            reply = retry

        return self._drop_repeats(reply, ctx)

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------
    @staticmethod
    def _image_mime(att: discord.Attachment) -> Optional[str]:
        ctype = (att.content_type or "").split(";")[0].strip().lower()
        if ctype in IMAGE_MIME_TYPES:
            return ctype
        name = att.filename.lower()
        return next((mime for ext, mime in IMAGE_EXTENSIONS.items() if name.endswith(ext)), None)

    @staticmethod
    def _sniff_mime(data: bytes) -> Optional[str]:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        return None

    def _collect_images(self, message: Optional[discord.Message]) -> list:
        """Image attachments on the message, or on the message it replies to."""
        if message is None:
            return []
        atts = list(message.attachments)
        ref = message.reference.resolved if message.reference else None
        if isinstance(ref, discord.Message):
            atts += ref.attachments
        return [a for a in atts if self._image_mime(a)][:MAX_IMAGES_PER_MESSAGE]

    async def _attachments_to_parts(self, attachments: list) -> list:
        """Download images and turn them into OpenAI-style image_url parts."""
        parts = []
        for att in attachments:
            if att.size > MAX_IMAGE_BYTES:
                logger.warning(f"Skipping image {att.filename!r}: too large")
                continue
            async with self.session.get(att.url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"image download failed: HTTP {resp.status}")
                data = await resp.read()
            # Trust the file's real bytes over Discord's reported type: a PNG
            # labelled image/webp (or vice versa) makes LM Studio reject the
            # data URL with "'url' field must be a base64 encoded image".
            mime = self._sniff_mime(data)
            if not mime:
                logger.warning(f"Skipping {att.filename!r}: not a recognised image format")
                continue
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        return parts

    # ------------------------------------------------------------------
    # Core chat
    # ------------------------------------------------------------------
    async def chat(self, channel_id: int, speaker: str, user_prompt: str,
                   attachments: Optional[list] = None, guild: Optional[discord.Guild] = None) -> str:
        """Add one message to the channel's shared conversation and return
        the reply. Raises ChatError on failure."""
        speaker = self._clean_name(speaker)
        text = self._clean_content(guild, user_prompt)

        ctx = self._get_context(channel_id)
        ctx.pending += 1
        try:
            async with ctx.lock:  # everyone in this channel is served in order
                if ctx.expired():
                    ctx.clear()
                ctx.language = detect_language(text) or ctx.language
                epoch = ctx.epoch
                hint = self._hint_for(ctx, speaker, text)

                # Images: the vision model describes, the text model replies.
                description, n_images = "", 0
                if attachments:
                    try:
                        parts = await self._attachments_to_parts(attachments)
                        if not parts:
                            raise RuntimeError("no usable images")
                        n_images = len(parts)
                        description = (await self._describe_images(parts, text))[:IMAGE_DESC_CHARS]
                    except Exception as e:
                        logger.error(f"Image handling failed: {e}")
                        raise ChatError("image") from e

                shown = text or ("(sent an image with no text)" if description else "")
                prompt_text = shown
                stored = shown
                if description:
                    prompt_text += (f"\n[The user attached {n_images} image(s). A vision model "
                                    f"describes them as: {description}]")
                    stored += f" [attached {n_images} image(s): {description}]"

                reply = await self._generate(ctx, speaker, prompt_text, text, hint)

                # Store only on success, and only if nobody reset the channel
                # while we were waiting for the model.
                if ctx.epoch == epoch:
                    ctx.exchanges.append({"speaker": speaker, "text": stored, "reply": reply})
                    ctx.last_active = time.monotonic()
                return reply
        finally:
            ctx.pending -= 1

    async def _reply_for(self, channel_id: int, speaker: str, prompt: str,
                         attachments: list, guild: Optional[discord.Guild]) -> str:
        """The model's reply, or an in-character error message in the
        channel's language. Never raises."""
        try:
            return await self.chat(channel_id, speaker, prompt, attachments, guild)
        except Exception as e:
            kind = e.kind if isinstance(e, ChatError) else "model"
            if not isinstance(e, ChatError):
                logger.error(f"AI request failed: {e}")
            ctx = self.contexts.get(channel_id)
            return ERRORS.get(ctx.language if ctx else "en", ERRORS["en"])[kind]

    @staticmethod
    async def _send_reply(reply: str, first_send, later_send):
        # Discord messages cap at 2000 chars; split long replies.
        for i in range(0, len(reply), 2000):
            chunk = reply[i:i + 2000]
            await (first_send(chunk) if i == 0 else later_send(chunk))

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------
    async def respond_to_mention(self, message: discord.Message, prompt: str):
        """Called from messages_cog.py when someone @-mentions the bot
        with free text (not "help" and not a known command name)."""
        async with message.channel.typing():
            reply = await self._reply_for(
                message.channel.id, message.author.display_name, prompt,
                self._collect_images(message), message.guild)
        await self._send_reply(reply, message.reply, message.channel.send)

    @commands.hybrid_command(name="chat", description="Ask the local AI model a question.")
    async def chat_command(self, ctx: commands.Context, image: Optional[discord.Attachment] = None,
                           *, prompt: str):
        await ctx.defer()  # local models can be slow - avoid the 3s slash-command timeout
        attachments = [image] if image and self._image_mime(image) else []
        if not attachments and ctx.message and not ctx.interaction:
            attachments = self._collect_images(ctx.message)
        reply = await self._reply_for(
            ctx.channel.id, ctx.author.display_name, prompt, attachments, ctx.guild)
        await self._send_reply(reply, ctx.reply, ctx.send)

    @commands.hybrid_command(name="chatreset", description="Clear this channel's shared AI conversation.")
    async def chatreset(self, ctx: commands.Context):
        # Clears the shared conversation for the WHOLE channel (all users).
        # Cleared in place + epoch bump so a reply still being generated
        # can't write itself back into the fresh conversation.
        context = self.contexts.get(ctx.channel.id)
        lang = context.language if context else "en"
        if context:
            context.clear()
        await ctx.reply(RESET_MESSAGES.get(lang, RESET_MESSAGES["en"]))


async def setup(bot):
    await bot.add_cog(AICog(bot))