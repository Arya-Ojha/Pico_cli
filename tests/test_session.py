"""Ticket 04 — the append-only session tree: append, fork, branch, persistence."""

from pico_core.session import (
    Node,
    Session,
    UserPayload,
    AssistantPayload,
    AssistantBlock,
)


def _assistant(text: str) -> AssistantPayload:
    return AssistantPayload(blocks=[AssistantBlock(kind="text", text=text)])


def test_append_creates_node_with_shape():
    session = Session()
    node = session.append(None, UserPayload(content="hello"))
    assert node.id
    assert node.parent_id is None
    assert node.timestamp
    assert node.payload == UserPayload(content="hello")
    assert session.active_leaf_id == node.id


def test_active_branch_is_root_to_leaf():
    session = Session()
    root = session.append(None, UserPayload(content="hi"))
    a = session.append(root.id, _assistant("hello"))
    b = session.append(a.id, _assistant("world"))
    branch = session.active_branch()
    assert [n.id for n in branch] == [root.id, a.id, b.id]


def test_fork_rewinds_and_keeps_old_nodes():
    session = Session()
    root = session.append(None, UserPayload(content="hi"))
    a = session.append(root.id, _assistant("first"))
    session.fork(root.id)
    assert session.active_leaf_id == root.id
    # old node is still present (append-only)
    assert a.id in session.nodes
    # new branch starts from the fork point
    b = session.append(root.id, _assistant("second"))
    assert [n.id for n in session.active_branch()] == [root.id, b.id]


def test_fork_unknown_node_raises():
    session = Session()
    try:
        session.fork("nope")
    except KeyError:
        return
    raise AssertionError("expected KeyError")


def test_session_without_duration_loads_as_unknown(tmp_path):
    import json

    session = Session()
    root = session.append(None, UserPayload(content="hi"))
    session.append(root.id, _assistant("hello"))
    # Simulate a pre-duration file: drop the field entirely.
    lines = []
    for line in session.to_jsonl().splitlines():
        data = json.loads(line)
        if "payload" in data:
            data["payload"].pop("duration_ms", None)
        lines.append(json.dumps(data))
    path = tmp_path / "old.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    loaded = Session.load(path)
    assistants = [n.payload for n in loaded.active_branch() if isinstance(n.payload, AssistantPayload)]
    assert assistants[0].duration_ms is None


def test_save_load_round_trip(tmp_path):
    session = Session()
    root = session.append(None, UserPayload(content="hi"))
    a = session.append(root.id, _assistant("hello"))
    path = tmp_path / "s.jsonl"
    session.save(path)
    loaded = Session.load(path)
    assert loaded.id == session.id
    assert loaded.active_leaf_id == a.id
    assert set(loaded.nodes) == set(session.nodes)
    assert [n.id for n in loaded.active_branch()] == [root.id, a.id]
