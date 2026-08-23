from agent.conversation_loop import _without_tool_definition


def test_request_local_tool_filter_removes_only_exact_skill_view():
    tools = [
        {"type": "function", "function": {"name": "skill_view"}},
        {"type": "function", "function": {"name": "terminal"}},
        {"type": "function", "function": {"name": "skills_list"}},
    ]

    assert _without_tool_definition(tools, "skill_view") == tools[1:]
