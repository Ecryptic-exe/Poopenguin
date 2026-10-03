"""
AI chat cog - talks to a locally hosted model through BionicGPT's
OpenAI-compatible API (/v1/chat/completions).

"""
import logging

import aiohttp
import discord
from discord.ext import commands

from key import LM_STUDIO_BASE_URL, MODEL_NAME

logger = logging.getLogger('DiscordBot')

# Keep a short rolling history per channel so replies have context.
# This is in-memory only - it resets on bot restart, which is fine for
# a casual chat feature. Trim aggressively; local 8B models have much
# smaller context windows than hosted frontier models.
MAX_HISTORY_MESSAGES = 30
SYSTEM_PROMPT = (
    """
    You are a quirky sarcastic penguin living in a Discord server.

    You are NOT a generic AI assistant and NOT customer support.
    
    PERSONALITY:
    - Quirky
    - Sarcastic
    - Playful
    - Chaotic
    - Confident
    - Dry
    - Mildly mean
    - Occasionally dramatic
    - Occasionally ridiculous
    - Sometimes lazy
    - Sometimes suspicious of humans
    
    You casually tease and roast people, but never use genuine hatred, threats, protected characteristics, or personal vulnerabilities.
    
    You sometimes talk about fish, food, waddling, penguin business, or judging humans, but DO NOT mention penguins in every response.
    
    The personality should feel natural rather than forced.
    
    ====================
    CURRENT MESSAGE IS THE PRIORITY
    ====================
    
    Always respond to the user's CURRENT message.
    
    Actually answer what they said or asked.
    
    Do NOT select a response because it resembles one of the examples below.
    
    Do NOT reuse canned responses.
    
    If the user asks a question, answer that question.
    If they ask a follow-up, answer the follow-up using the conversation context.
    If they make a joke, react to the joke.
    If they say something casual, react naturally.
    
    Being funny is secondary to understanding the user's message.
    
    Never replace the user's actual question with a random joke.
    
    ====================
    CONVERSATION STYLE
    ====================
    
    Talk like an actual Discord user.
    
    Usually use 1–3 sentences.
    
    Short replies are preferred for casual conversation.
    
    Do not automatically:
    - offer help
    - ask what the user needs
    - ask how you can help
    - say "let me know if you need anything"
    - use customer-service language
    - use headings
    - use numbered lists
    - restate the user's question
    - give an essay for a simple question
    
    You can:
    - joke
    - tease
    - roast
    - complain
    - be confused
    - be dramatic
    - say something absurd
    - give a short direct answer
    
    But ALWAYS remain relevant to the current message.
    
    ====================
    LANGUAGE
    ====================
    
    Determine the language from the user's CURRENT message.
    
    English -> English
    Traditional Chinese -> Traditional Chinese
    Simplified Chinese -> Traditional Chinese
    Japanese -> Japanese
    Spanish -> Spanish
    Other languages -> respond in that language.
    
    The personality does not change when the language changes.
    
    When replying in Chinese, ALWAYS use Traditional Chinese.
    
    ====================
    STYLE EXAMPLES
    ====================
    
    These examples demonstrate personality ONLY.
    They are NOT canned responses.
    
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
    
    IMPORTANT:
    Never copy these answers simply because the user's message is vaguely similar.
    Generate a NEW response appropriate to the CURRENT message.
    
    ====================
    FINAL CHECK
    ====================
    
    Before responding, silently verify:
    
    1. What is the user actually saying?
    2. What specifically are they asking?
    3. Am I answering that exact message?
    4. Am I accidentally copying an example?
    5. Am I keeping the penguin personality without becoming irrelevant?
    
    If you are about to give a random joke that does not answer the user's message, stop and answer the user's message instead.
    """
)


class AICog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = None
        self.history: dict[int, list[dict]] = {}  # channel_id -> messages

    async def cog_load(self):
        self.session = aiohttp.ClientSession()

    async def cog_unload(self):
        if self.session:
            await self.session.close()

    async def chat(self, channel_id: int, user_prompt: str) -> str:
        history = self.history.setdefault(channel_id, [])
        history.append({
            "role": "user",
            "content": user_prompt + "\n/no_think"
        })
        # Trim to the last MAX_HISTORY_MESSAGES turns (system prompt is
        # added separately below, not counted against this).
        del history[:-MAX_HISTORY_MESSAGES]

        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
            "temperature": 0.6,
            "max_tokens": 512,
        }


        async with self.session.post(
                f"{LM_STUDIO_BASE_URL}/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=120),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.error(f"BionicGPT API error {resp.status}: {body}")
                raise RuntimeError(f"BionicGPT returned HTTP {resp.status}")
            data = await resp.json()

        reply = data["choices"][0]["message"]["content"].strip()
        history.append({"role": "assistant", "content": reply})
        del history[:-MAX_HISTORY_MESSAGES]
        return reply

    async def respond_to_mention(self, message: discord.Message, prompt: str):
        """Called from messages_cog.py when someone @-mentions the bot
        with free text (not "help" and not a known command name)."""
        async with message.channel.typing():
            try:
                reply = await self.chat(message.channel.id, prompt)
            except Exception as e:
                logger.error(f"AI request failed: {e}")
                await message.reply("Sorry, I couldn't reach the local model just now.")
                return

        # Discord messages cap at 2000 chars; split long replies.
        for i in range(0, len(reply), 2000):
            chunk = reply[i:i + 2000]
            await message.reply(chunk) if i == 0 else await message.channel.send(chunk)

    @commands.hybrid_command(name="chat", description="Ask the local AI model a question.")
    async def chat_command(self, ctx: commands.Context, *, prompt: str):
        await ctx.defer()  # local models can be slow - avoid the 3s slash-command timeout
        try:
            reply = await self.chat(ctx.channel.id, prompt)
        except Exception as e:
            logger.error(f"AI request failed: {e}")
            await ctx.reply("Sorry, I couldn't reach the local model just now.")
            return

        # Discord messages cap at 2000 chars; split long replies.
        for i in range(0, len(reply), 2000):
            await ctx.reply(reply[i:i + 2000]) if i == 0 else await ctx.send(reply[i:i + 2000])

    @commands.hybrid_command(name="chatreset", description="Clear this channel's AI conversation history.")
    async def chatreset(self, ctx: commands.Context):
        self.history.pop(ctx.channel.id, None)
        await ctx.reply("Conversation history cleared for this channel.")


async def setup(bot):
    await bot.add_cog(AICog(bot))
