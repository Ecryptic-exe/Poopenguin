from copy import deepcopy

from workspace.publish_entrypoints import command_shape


def test_discord_channel_type_order_is_not_a_functional_change():
    current = {
        "name": "permissions",
        "description": "test",
        "options": [{"name": "channel", "type": 7, "channel_types": [0, 2, 4, 5, 13]}],
    }
    proposed = deepcopy(current)
    proposed["options"][0]["channel_types"] = [13, 2, 0, 5, 4]
    assert command_shape(current) == command_shape(proposed)
    proposed["options"][0]["channel_types"].remove(4)
    assert command_shape(current) != command_shape(proposed)


def test_discord_context_menu_empty_description_is_not_a_schema_change():
    assert command_shape(
        {"name": "View your score", "type": 3, "description": ""}
    ) == command_shape({"name": "View your score", "type": 3})


def test_functional_option_and_choice_order_changes_are_still_detected():
    current = {
        "name": "pick",
        "description": "test",
        "options": [
            {
                "name": "choices",
                "type": 3,
                "choices": [
                    {"name": "First", "value": "a"},
                    {"name": "Second", "value": "b"},
                ],
            }
        ],
    }
    proposed = deepcopy(current)
    proposed["options"][0]["choices"].reverse()
    assert command_shape(current) != command_shape(proposed)
    proposed = deepcopy(current)
    proposed["options"][0]["required"] = True
    assert command_shape(current) != command_shape(proposed)
