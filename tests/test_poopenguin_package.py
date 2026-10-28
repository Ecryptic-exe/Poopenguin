from __future__ import annotations

import ast
import copy
import importlib
import json
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from discord.ext import commands
from discord.utils import MISSING

from chuni_penguin.application.extensions import (
    ExtensionManager,
    ExtensionSpec,
    ExtensionState,
)
from chuni_penguin.cogs import DEFAULT_EXTENSIONS

_ROOT = Path(__file__).resolve().parents[1]
_MODULES = (
    "admin_cog",
    "ai_cog",
    "ai_settings",
    "config",
    "copypasta_cog",
    "copypasta_manager",
    "feed_cog",
    "gacha_cog",
    "gacha_manager",
    "general_cog",
    "i18n",
    "keyword_manager",
    "keywords_cog",
    "messages_cog",
    "vote_cog",
)
_EXPECTED_COMMANDS = {
    "admin_cog": {
        ("command", "setperms"),
        ("command", "autoreact"),
        ("command", "autoban"),
        ("command", "lang"),
    },
    "ai_cog": {("command", "chat"), ("command", "chatreset")},
    "copypasta_cog": {
        ("group", "copypasta"),
        ("subcommand", "list"),
        ("subcommand", "info"),
        ("subcommand", "create"),
        ("subcommand", "delete"),
        ("subcommand", "enable"),
        ("subcommand", "disable"),
        ("subcommand", "add"),
        ("subcommand", "remove"),
    },
    "feed_cog": {
        ("group", "feed"),
        ("subcommand", "add"),
        ("subcommand", "remove"),
        ("subcommand", "list"),
        ("subcommand", "check"),
    },
    "gacha_cog": {
        ("group", "gacha"),
        ("subcommand", "pull"),
        ("subcommand", "target"),
        ("subcommand", "stats"),
        ("subcommand", "reset"),
        ("subcommand", "browse"),
        ("subcommand", "pool"),
        ("subcommand", "setfeatured"),
        ("subcommand", "setimage"),
        ("subcommand", "removeimage"),
        ("subcommand", "setthumbnail"),
        ("subcommand", "removethumbnail"),
        ("subcommand", "reload"),
    },
    "general_cog": {
        ("command", "ask"),
        ("command", "pick"),
        ("command", "rng"),
        ("command", "rcg"),
    },
    "keywords_cog": {
        ("group", "keyword"),
        ("subcommand", "list"),
        ("subcommand", "info"),
        ("subcommand", "create"),
        ("subcommand", "delete"),
        ("subcommand", "enable"),
        ("subcommand", "disable"),
        ("subcommand", "addkeyword"),
        ("subcommand", "removekeyword"),
        ("subcommand", "addresponse"),
        ("subcommand", "removeresponse"),
        ("subcommand", "rate"),
    },
    "messages_cog": {("listener", "on_message")},
    "vote_cog": {
        ("command", "vto"),
        ("command", "setvote"),
        ("listener", "on_raw_reaction_add"),
        ("listener", "on_raw_reaction_remove"),
        ("listener", "on_raw_reaction_clear"),
        ("listener", "on_raw_reaction_clear_emoji"),
        ("listener", "on_raw_message_delete"),
        ("listener", "on_raw_bulk_message_delete"),
    },
}


class _Message:
    def __init__(self):
        self.id = 701
        self.reactions = []
        self.payloads = []

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)

    async def delete(self):
        return None

    async def edit(self, **kwargs):
        self.payloads.append(kwargs)
        return self


class _Bot:
    def __init__(self, owner_id=11):
        self.owner_id = owner_id

    async def is_owner(self, user):
        return user.id == self.owner_id


class _Context:
    def __init__(self, owner_id=11):
        self.author = self.user = SimpleNamespace(
            id=owner_id,
            name="Fixture",
            display_name="Fixture",
            mention=f"<@{owner_id}>",
        )
        self.guild = SimpleNamespace(
            id=33,
            me=SimpleNamespace(
                guild_permissions=SimpleNamespace(
                    add_reactions=True,
                    ban_members=True,
                    manage_channels=True,
                    moderate_members=True,
                )
            ),
        )
        self.channel = SimpleNamespace(id=456, mention="<#456>")
        self.bot = _Bot(owner_id)
        self.interaction = None
        self.sent = []
        self.deferred = False

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})
        return _Message()

    async def reply(self, content=None, **kwargs):
        return await self.send(content, **kwargs)

    async def defer(self, **kwargs):
        self.deferred = True


class _Interaction:
    def __init__(self, user_id):
        self.user = SimpleNamespace(id=user_id)
        self.response = SimpleNamespace(
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
            is_done=Mock(return_value=False),
        )
        self.delete_original_response = AsyncMock()
        self.edit_original_response = AsyncMock()


def _json_store(path: Path, default):
    def load():
        if not path.exists():
            return copy.deepcopy(default)
        return json.loads(path.read_text(encoding="utf-8"))

    def save(value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    return load, save


def _decorator_inventory(module_name):
    path = _ROOT / "chuni_penguin" / "cogs" / "poopenguin" / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cog = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Cog")
    )
    found = set()
    for method in cog.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in method.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            decorator_name = ast.unparse(decorator.func)
            if decorator_name in ("commands.hybrid_command", "commands.command"):
                kind = "command"
            elif decorator_name in ("commands.hybrid_group", "commands.group"):
                kind = "group"
            elif decorator_name.endswith(".command"):
                kind = "subcommand"
            elif decorator_name == "commands.Cog.listener":
                kind = "listener"
            else:
                continue
            name_arg = next(
                (
                    keyword.value
                    for keyword in decorator.keywords
                    if keyword.arg == "name"
                ),
                None,
            )
            name = name_arg.value if isinstance(name_arg, ast.Constant) else method.name
            found.add((kind, name))
    return found


def test_legacy_imports_are_identity_aliases_and_extensions_resolve_old_keys(
    monkeypatch,
):
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LM_STUDIO_MODEL", "offline-test-model")
    for name in _MODULES:
        old_path = f"chuni_penguin.legacy_b.{name}"
        canonical_path = f"chuni_penguin.cogs.poopenguin.{name}"
        old_module = importlib.import_module(old_path)
        canonical_module = importlib.import_module(canonical_path)
        assert old_module is canonical_module
        assert sys.modules[old_path] is canonical_module

    specs = [
        spec
        for spec in DEFAULT_EXTENSIONS
        if spec.module.startswith("chuni_penguin.cogs.poopenguin.")
    ]
    assert len(specs) == 9
    manager = ExtensionManager(SimpleNamespace(), DEFAULT_EXTENSIONS)
    for spec in specs:
        legacy_key = f"chuni_penguin.legacy_b.{spec.module.rsplit('.', 1)[-1]}"
        assert spec.aliases == (legacy_key,)
        assert manager.resolve(legacy_key) is spec
        assert manager.report.extensions.get(legacy_key) is None

    optional = {spec.key for spec in specs if spec.optional}
    assert optional == {
        "poopenguin.messages_cog",
        "poopenguin.vote_cog",
        "poopenguin.admin_cog",
        "poopenguin.feed_cog",
        "poopenguin.ai_cog",
    }
    assert all(not spec.default_enabled for spec in specs if spec.optional)
    assert all(spec.default_enabled for spec in specs if not spec.optional)

    selected = manager.selected_specs(
        enabled={"chuni_penguin.legacy_b.ai_cog"}, disabled=set()
    )
    assert [spec.module for spec in selected] == [
        "chuni_penguin.cogs.poopenguin.ai_cog"
    ]
    status = manager._status(specs[-1], ExtensionState.DISABLED)
    legacy_key = "chuni_penguin.legacy_b.ai_cog"
    canonical_key = "chuni_penguin.cogs.poopenguin.ai_cog"
    assert manager.report.extensions[legacy_key] is status
    assert manager.report.extensions.get(legacy_key) is status
    assert list(manager.report.extensions) == [canonical_key]
    assert list(manager.report.as_dict()["extensions"]) == [canonical_key]
    assert legacy_key not in manager.report.as_dict()["extensions"]
    assert manager.report.as_dict()["extensions"][canonical_key]["state"] == "disabled"


@pytest.mark.asyncio
async def test_legacy_module_alias_tracks_extension_reload_unload_and_rollback(
    monkeypatch, tmp_path
):
    import discord

    suffix = f"{id(tmp_path):x}"
    canonical_package = f"poopenguin_lifecycle_{suffix}"
    legacy_package = f"poopenguin_legacy_lifecycle_{suffix}"
    canonical_name = f"{canonical_package}.extension"
    legacy_name = f"{legacy_package}.extension"
    canonical_dir = tmp_path / canonical_package
    legacy_dir = tmp_path / legacy_package
    canonical_dir.mkdir()
    legacy_dir.mkdir()
    (canonical_dir / "__init__.py").write_text("", encoding="utf-8")
    (legacy_dir / "__init__.py").write_text("", encoding="utf-8")
    failure_flag = canonical_dir / "fail_reload"
    (canonical_dir / "extension.py").write_text(
        "from pathlib import Path\n"
        "from discord.ext import commands\n"
        "class LifecycleCog(commands.Cog):\n"
        "    pass\n"
        "async def setup(bot):\n"
        "    failure_flag = Path(__file__).with_name('fail_reload')\n"
        "    if failure_flag.exists():\n"
        "        failure_flag.unlink()\n"
        "        raise RuntimeError('expected reload failure')\n"
        "    await bot.add_cog(LifecycleCog())\n",
        encoding="utf-8",
    )
    (legacy_dir / "extension.py").write_text(
        f"import importlib as _importlib\n"
        f"import sys as _sys\n"
        f"_canonical = _importlib.import_module('{canonical_name}')\n"
        f"_sys.modules[__name__] = _canonical\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    manager = ExtensionManager(
        commands.Bot(command_prefix="!", intents=discord.Intents.none()),
        (
            ExtensionSpec(
                canonical_name,
                ("LifecycleCog",),
                aliases=(legacy_name,),
            ),
        ),
    )
    bot = manager.bot

    try:
        await bot.load_extension(canonical_name)
        first = importlib.import_module(legacy_name)
        assert first is sys.modules[canonical_name]
        assert sys.modules[canonical_package].extension is first
        assert sys.modules[legacy_package].extension is first
        assert set(bot.extensions) == {canonical_name}

        await bot.reload_extension(canonical_name)
        second = importlib.import_module(legacy_name)
        assert second is sys.modules[canonical_name]
        assert second is not first
        assert sys.modules[canonical_package].extension is second
        assert sys.modules[legacy_package].extension is second
        assert set(bot.extensions) == {canonical_name}

        failure_flag.touch()
        with pytest.raises(commands.ExtensionFailed, match="expected reload failure"):
            await bot.reload_extension(canonical_name)
        restored = sys.modules[canonical_name]
        assert importlib.import_module(legacy_name) is restored
        assert sys.modules[canonical_package].extension is restored
        assert sys.modules[legacy_package].extension is restored
        assert bot.get_cog("LifecycleCog") is not None
        assert set(bot.extensions) == {canonical_name}

        await bot.unload_extension(canonical_name)
        assert canonical_name not in sys.modules
        assert legacy_name not in sys.modules
        assert not hasattr(sys.modules[canonical_package], "extension")
        assert not hasattr(sys.modules[legacy_package], "extension")
        assert not bot.extensions

        canonical_parent = __import__(canonical_package, fromlist=["extension"])
        reimported = canonical_parent.extension
        assert reimported is sys.modules[canonical_name]
        assert sys.modules[canonical_package].extension is reimported
        assert importlib.import_module(legacy_name) is reimported
        assert sys.modules[legacy_package].extension is reimported
        assert not bot.extensions
        await bot.load_extension(canonical_name)
        assert importlib.import_module(legacy_name) is sys.modules[canonical_name]
        assert set(bot.extensions) == {canonical_name}
    finally:
        await bot.close()
        for module_name in tuple(sys.modules):
            if module_name.startswith((canonical_package, legacy_package)):
                sys.modules.pop(module_name, None)


def test_original_non_chunithm_cog_decorator_inventory_is_complete():
    for module_name, expected in _EXPECTED_COMMANDS.items():
        assert _decorator_inventory(module_name) == expected, module_name
    # Global help and command-tree sync belong to the shared core extensions.
    assert not any(
        name in {"help", "sync"}
        for inventory in _EXPECTED_COMMANDS.values()
        for _, name in inventory
    )


def test_hybrid_parameter_defaults_and_legacy_aliases_are_unchanged():
    from chuni_penguin.cogs.poopenguin.copypasta_cog import CopypastaCog
    from chuni_penguin.cogs.poopenguin.gacha_cog import GachaCog
    from chuni_penguin.cogs.poopenguin.general_cog import GeneralCog

    def params(command):
        return [(p.name, p.required, p.default) for p in command.app_command.parameters]

    assert params(GeneralCog.pick) == [("choices", False, None)]
    assert params(GeneralCog.rng) == [
        ("min_val", False, "1"),
        ("max_val", False, "100"),
        ("type", False, "int"),
    ]
    assert params(GachaCog.gacha_target) == [("character", False, None)]
    assert params(GachaCog.gacha_setimage) == [
        ("character", True, MISSING),
        ("url", True, MISSING),
    ]
    assert CopypastaCog.copypasta.aliases == ["cp"]
    assert CopypastaCog.copypasta_info.aliases == ["show"]
    assert GachaCog.gacha_stats.aliases == ["results"]


@pytest.mark.asyncio
async def test_general_random_pick_and_rng_behaviors(monkeypatch):
    from chuni_penguin.cogs.poopenguin import general_cog

    cog = general_cog.GeneralCog(SimpleNamespace())
    cog._lang = lambda _: "english"
    ctx = _Context()
    monkeypatch.setattr(general_cog.random, "choice", lambda choices: choices[-1])
    await general_cog.GeneralCog.pick.callback(
        cog, ctx, choices='"New York" "Los Angeles"'
    )
    assert "Los Angeles" in ctx.sent[-1]["content"]

    monkeypatch.setattr(general_cog.random, "randint", lambda low, high: low)
    await general_cog.GeneralCog.rng.callback(
        cog, ctx, min_val="5", max_val="9", type="int"
    )
    assert "5" in ctx.sent[-1]["content"]
    await general_cog.GeneralCog.rng.callback(
        cog, ctx, min_val="5", max_val="4", type="int"
    )
    assert "less than or equal" in ctx.sent[-1]["content"]

    monkeypatch.setattr(general_cog.random, "random", lambda: 0.99)
    await general_cog.GeneralCog.ask.callback(cog, ctx, question="will this work")
    assert "very likely" in ctx.sent[-1]["content"]


@pytest.mark.asyncio
async def test_gacha_target_pull_stats_reset_artwork_and_close_rebuild(
    monkeypatch, tmp_path
):
    from chuni_penguin.cogs.poopenguin import gacha_manager
    from chuni_penguin.cogs.poopenguin.gacha_cog import GachaCog
    from chuni_penguin.ui.dismiss import CloseButton

    pool = {
        "rates": {"three_star": 1.6, "two_star": 8.4, "one_star": 90.0},
        "featured": "Target",
        "three_star": ["Target", "Other"],
        "two_star": ["Two"],
        "one_star": ["One"],
    }
    pool_load, pool_save = _json_store(tmp_path / "pool.json", pool)
    users_load, users_save = _json_store(tmp_path / "users.json", {"users": {}})
    images_load, images_save = _json_store(tmp_path / "images.json", {})
    for name, value in (
        ("load_gacha_pool", pool_load),
        ("save_gacha_pool", pool_save),
        ("load_gacha_users", users_load),
        ("save_gacha_users", users_save),
        ("load_gacha_images", images_load),
        ("save_gacha_images", images_save),
    ):
        monkeypatch.setattr(gacha_manager, name, value)

    cog = GachaCog(SimpleNamespace())
    cog._lang = lambda _: "english"
    ctx = _Context()
    await GachaCog.gacha_target.callback(cog, ctx, character="Target")
    target_view = ctx.sent[-1]["view"]
    await target_view.confirm_button.callback(_Interaction(22))
    assert cog.manager.get_user_target(11) is None
    await target_view.confirm_button.callback(_Interaction(11))
    assert cog.manager.get_user_target(11) == "Target"

    cog.manager._reload_users()
    cog.manager._users["users"]["11"]["pity_counter"] = 199
    cog.manager._save_users()
    monkeypatch.setattr(gacha_manager.random, "uniform", lambda *_: 99.0)
    results, record = cog.manager.pull("11")
    assert results[0]["forced"] is True
    assert results[0]["character"] == "Target"
    assert record["total_pulls"] == 1 and record["pity_counter"] == 0

    await GachaCog.gacha_stats.callback(cog, ctx)
    assert "Total Pulls: **1**" in ctx.sent[-1]["content"]
    assert any(
        "Target" in (embed.description or "") for embed in ctx.sent[-1]["embeds"]
    )

    await GachaCog.gacha_reset.callback(cog, ctx)
    reset_view = ctx.sent[-1]["view"]
    await reset_view.confirm_button.callback(_Interaction(22))
    assert cog.manager.get_user(11)["total_pulls"] == 1
    await reset_view.confirm_button.callback(_Interaction(11))
    assert cog.manager.get_user(11)["total_pulls"] == 0

    cog.manager.set_image("Target", "https://assets.example/target.png")
    await GachaCog.gacha_browse.callback(cog, ctx)
    embed = ctx.sent[-1]["embed"]
    assert embed.image.url == "https://assets.example/target.png"
    menu = ctx.sent[-1]["view"]
    assert sum(isinstance(item, CloseButton) for item in menu.children) == 1
    menu._build_target()
    menu._build_main()
    assert sum(isinstance(item, CloseButton) for item in menu.children) == 1
    menu.stop()

    await GachaCog.gacha_pool.callback(cog, ctx)
    assert "3-Star Roster" in [field.name for field in ctx.sent[-1]["embed"].fields]


@pytest.mark.asyncio
async def test_template_and_keyword_crud_owner_checks_and_menu_ownership(
    monkeypatch, tmp_path
):
    from chuni_penguin.cogs.poopenguin import copypasta_manager, keyword_manager
    from chuni_penguin.cogs.poopenguin.copypasta_cog import CopypastaCog, CopypastaMenu
    from chuni_penguin.cogs.poopenguin.keywords_cog import KeywordMenu, KeywordsCog

    pasta_load, pasta_save = _json_store(tmp_path / "copypasta.json", {"types": {}})
    keyword_load, keyword_save = _json_store(tmp_path / "keywords.json", {"sets": {}})
    monkeypatch.setattr(copypasta_manager, "load_copypasta", pasta_load)
    monkeypatch.setattr(copypasta_manager, "save_copypasta", pasta_save)
    monkeypatch.setattr(keyword_manager, "load_keywords", keyword_load)
    monkeypatch.setattr(keyword_manager, "save_keywords", keyword_save)
    monkeypatch.setattr(copypasta_manager.random, "randrange", lambda count: 0)
    monkeypatch.setattr(keyword_manager.random, "uniform", lambda *_: 0)
    monkeypatch.setattr(keyword_manager.random, "choice", lambda values: values[0])

    pasta = copypasta_manager.CopypastaManager()
    pasta.create_type("tag")
    pasta.add_template("tag", "{text} is great")
    assert pasta.pick("tag", ["Penguin"])[1] == "Penguin is great"
    pasta.remove_template("tag", 0)
    assert pasta.get_type("tag")["templates"] == []
    pasta.delete_type("tag")

    keywords = keyword_manager.KeywordManager()
    keywords.create_set("greeting")
    keywords.add_keyword("greeting", "hello")
    keywords.add_response("greeting", "Hi there")
    keywords.set_trigger_rate("greeting", 100)
    assert keywords.find_match("HELLO everyone") == ("greeting", "Hi there")
    keywords.remove_keyword("greeting", "hello")
    keywords.remove_response("greeting", 0)
    assert keywords.get_set("greeting")["keywords"] == []
    assert keywords.get_set("greeting")["responses"] == []

    owner_ctx = _Context(11)
    outsider_ctx = _Context(22)
    outsider_ctx.bot = _Bot(owner_id=11)
    for command in (KeywordsCog.keyword_create, CopypastaCog.copypasta_create):
        assert len(command.checks) == 1
        assert await command.checks[0](owner_ctx)
        with pytest.raises(commands.NotOwner):
            await command.checks[0](outsider_ctx)

    keyword_menu = KeywordMenu(owner_ctx, keywords.list_sets(), "english")
    pasta_menu = CopypastaMenu(owner_ctx, pasta.list_types(), "english")
    assert not await keyword_menu.interaction_check(_Interaction(22))
    assert not await pasta_menu.interaction_check(_Interaction(22))
    keyword_menu.stop()
    pasta_menu.stop()


@pytest.mark.asyncio
async def test_optional_cogs_use_mocked_storage_and_no_external_actions(
    monkeypatch, tmp_path
):
    from chuni_penguin.cogs.poopenguin import (
        admin_cog,
        feed_cog,
        messages_cog,
        vote_cog,
    )

    settings = {"required_votes": 3, "admin_only": False, "language": {}}
    saved_settings = []
    monkeypatch.setattr(admin_cog, "load_settings", lambda: copy.deepcopy(settings))
    monkeypatch.setattr(
        admin_cog, "save_settings", lambda value: saved_settings.append(value)
    )
    admin_ctx = _Context()
    admin = admin_cog.AdminCog(SimpleNamespace())
    admin._lang = lambda _: "english"
    await admin_cog.AdminCog.autoban.callback(
        admin, admin_ctx, reason="fixture reason", delete_days=1
    )
    assert saved_settings[-1]["autoban"]["456"] == {
        "reason": "fixture reason",
        "delete_days": 1,
    }

    saved_autoreact = []
    monkeypatch.setattr(
        messages_cog,
        "load_settings",
        lambda: {"autoreact": {"456": "🎵"}},
    )
    monkeypatch.setattr(messages_cog, "save_settings", saved_autoreact.append)
    message_cog = messages_cog.MessagesCog(SimpleNamespace())
    message = SimpleNamespace(
        channel=SimpleNamespace(id=456),
        author=SimpleNamespace(id=11),
        add_reaction=AsyncMock(),
    )
    await message_cog._handle_autoreact(message)
    message.add_reaction.assert_awaited_once_with("🎵")
    assert saved_autoreact[-1]["autoreact"]["456"]["emoji"] == "🎵"

    vote_saved = []
    monkeypatch.setattr(vote_cog, "load_settings", lambda: copy.deepcopy(settings))
    monkeypatch.setattr(vote_cog, "save_settings", vote_saved.append)
    vote = vote_cog.VoteCog(SimpleNamespace())
    vote_ctx = _Context()
    await vote_cog.VoteCog.setvote.callback(vote, vote_ctx, arg="admin")
    assert vote_saved[-1]["admin_only"] is True
    assert vote_cog.parse_time("30m") == timedelta(minutes=30)
    assert vote_cog.parse_time("nonsense") is None

    feeds = {}
    feed_saved = []
    monkeypatch.setattr(feed_cog, "_load_feeds", lambda: copy.deepcopy(feeds))
    monkeypatch.setattr(
        feed_cog, "_save_feeds", lambda value: feed_saved.append(copy.deepcopy(value))
    )

    class FakeProvider:
        name = "bluesky"

        def parse_account(self, raw):
            return raw.strip().lstrip("@")

        def fetch(self, account):
            return []

    monkeypatch.setattr(feed_cog, "PROVIDERS", {"bluesky": FakeProvider()})
    feed = object.__new__(feed_cog.FeedCog)
    feed.bot = SimpleNamespace()
    feed_ctx = _Context()
    await feed_cog.FeedCog.feed_add.callback(
        feed, feed_ctx, account="@fixture.example", channel=None, platform="bluesky"
    )
    assert feed_ctx.deferred
    assert feed_saved[-1]["33"][0]["account"] == "fixture.example"
    assert feed_saved[-1]["33"][0]["last_seen"] == ""

    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LM_STUDIO_MODEL", "offline-test-model")
    ai = importlib.import_module("chuni_penguin.cogs.poopenguin.ai_cog")
    assert ai.detect_language("こんにちは") == "ja"
    assert (
        ai.AICog._clean_reply("<think>private notes</think>Penguin: hello") == "hello"
    )

    class FakeResponse:
        status = 200

        async def json(self):
            return {"choices": [{"message": {"content": "mocked local reply"}}]}

    class FakeRequest:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_):
            return None

    class FakeSession:
        def post(self, url, **kwargs):
            assert url == "http://127.0.0.1:1234/v1/chat/completions"
            assert kwargs["json"] == {"messages": []}
            return FakeRequest()

    ai_cog = ai.AICog(SimpleNamespace())
    ai_cog.session = FakeSession()
    assert await ai_cog._complete({"messages": []}) == "mocked local reply"
