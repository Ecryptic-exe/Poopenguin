# 🐧 Poopenguin Discord Bot

**Poopenguin** is a quirky, chaos-loving bot built for the CHUNITHM community —
part moderation tool, part group-chat troublemaker, part gacha machine.
Perfect for servers that thrive on spontaneous nonsense.

Every command works as both a `!prefix` command and a `/slash` command.
Type `!help` in Discord for the full in-bot manual (English or Chinese).

## 💬 Features

- **Timeout Voting System**
  When a timeout-worthy moment strikes, members vote it into existence.
  Poopenguin tallies the votes and enforces the timeout once consensus is reached.

- **Mimicry Trigger**
  Three different users send the **exact same message** back to back?
  Poopenguin hops on the bandwagon and echoes it too.

- **Keyword & Copypasta Triggers**
  Say the wrong (right?) word and Poopenguin fires back a copypasta or
  keyword-triggered response to stir the pot further. Fully customizable —
  add, remove, and manage keyword sets and copypasta templates live, no
  redeploy required.

- **CHUNITHM Rating Charts** 📊
  `!rating <song> [difficulty]` looks up a song and draws how players of
  different ratings score on that chart. `!song_search <query>` finds songs
  and their available difficulties. Statistics come from the public Chunirec
  site, and only the chart you pick is fetched and drawn.

- **Gacha System** 🎰
  Pull for CHUNITHM-themed characters with configurable rates, set a pull
  target, and check your stats. Admins can hand-edit the roster, rates, and
  featured pick at any time.

- **Chatbot System** 💬
  @mention Poopenguin or use `!chat` and the penguin will reply to you. Each
  channel shares one conversation (`!chatreset` clears it), and it can look at
  images you attach. Runs on a local model through LM Studio.

- **Question Response**
  Ask a yes/no question and get an answer, weighted by a random success rate.

- **Random Choice**
  Feed it a list of options and let it pick one for you.

- **Random Number Generator**
  Generate a random number within a range you set.

- **Random Colour Generator**
  Conjure up a random colour on demand.

- **Auto Reaction**
  Auto-react with an emoji of your choice in a specific channel — optionally
  restricted to a specific user.

**[ADMIN ONLY]**

- **Channel Permission Recovery**
  Accidentally locked the bot out of a channel? Quick fix, no drama.

- **Keyword Set Management**
  Add, remove, enable, or disable entire keyword sets and their trigger words
  on the fly.

- **Autoban**
  Turn a channel into a honeypot: anyone who sends a message there is banned.
  Use with care.

- **Language Switching**
  Set the bot's response language (English / Chinese) per server.

## 🤖 Why "Poopenguin"?

Because CHUNITHM players deserve a little chaos, a lot of camaraderie, and a
penguin with an attitude problem.

## 📦 Requirements

- Python 3.9+
- [discord.py](https://pypi.org/project/discord.py/) 2.x
- Extra packages for the CHUNITHM charts (python-dotenv, requests,
  beautifulsoup4, numpy, matplotlib, Pillow, tzdata), all listed in
  `requirements.txt`
- [LM Studio](https://lmstudio.ai/) — only needed for the chatbot

## 🚀 Quick start

```
pip install -r requirements.txt
python bot.py
```

Create a `key.py` next to `bot.py` with your bot token (`api = "..."`) and your
LM Studio settings. `key.py` is never committed. The full setup guide, project
layout and command reference are in the project `README.md`.

---

*Note from the author: Code for Fun.* 🐧💩
