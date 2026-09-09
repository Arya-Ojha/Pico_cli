"""Append-only, tree-based session model (ADR-0002).

A session is a tree of immutable nodes. Each node carries an id, a parent
pointer, a timestamp, and a payload. Nodes are never edited or deleted — only
built upon. Branches are root-to-leaf timelines; forking rewinds to an earlier
node and starts a new branch.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from pico_ai.types import ToolCall, Usage


def new_id() -> str:
    """Return a fresh, unique node/session id."""
    return uuid.uuid4().hex


def utc_now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class UserPayload(BaseModel):
    """A user message."""

    kind: Literal["user"] = "user"
    content: str


class AssistantBlock(BaseModel):
    """A block of an assistant response: prose or reasoning."""

    kind: Literal["text", "thinking"]
    text: str = ""
    thinking: str = ""


class AssistantPayload(BaseModel):
    """An assistant response, block-granular (text / thinking)."""

    kind: Literal["assistant"] = "assistant"
    blocks: list[AssistantBlock] = Field(default_factory=list)
    usage: Usage | None = None

    @property
    def text(self) -> str:
        return "".join(b.text for b in self.blocks if b.kind == "text")


class ToolRequestPayload(BaseModel):
    """A request to run a tool."""

    kind: Literal["tool_request"] = "tool_request"
    tool_call: ToolCall


class ToolResultPayload(BaseModel):
    """The output of running a tool."""

    kind: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str
    content: str
    is_error: bool = False


class CompactionSummaryPayload(BaseModel):
    """A summary of older turns produced by compaction."""

    kind: Literal["compaction_summary"] = "compaction_summary"
    summary: str


Payload = Annotated[
    UserPayload
    | AssistantPayload
    | ToolRequestPayload
    | ToolResultPayload
    | CompactionSummaryPayload,
    Field(discriminator="kind"),
]


class Node(BaseModel):
    """An immutable, append-only unit of event data in a session."""

    id: str = Field(default_factory=new_id)
    parent_id: str | None = None
    timestamp: str = Field(default_factory=utc_now)
    payload: Payload


class Session(BaseModel):
    """A tree of nodes with a single active branch."""

    id: str = Field(default_factory=new_id)
    nodes: dict[str, Node] = Field(default_factory=dict)
    active_leaf_id: str | None = None

    # -- construction -------------------------------------------------------

    def append(self, parent_id: str | None, payload: Payload) -> Node:
        """Append a node as a child of ``parent_id`` and make it the active leaf."""
        if parent_id is not None and parent_id not in self.nodes:
            raise KeyError(f"unknown parent node id: {parent_id}")
        node = Node(parent_id=parent_id, payload=payload)
        self.nodes[node.id] = node
        self.active_leaf_id = node.id
        return node

    def fork(self, node_id: str) -> None:
        """Rewind to ``node_id``, starting a new branch from it."""
        if node_id not in self.nodes:
            raise KeyError(f"unknown node id: {node_id}")
        self.active_leaf_id = node_id

    # -- traversal ----------------------------------------------------------

    def active_branch(self) -> list[Node]:
        """Return the root-to-leaf timeline of the active branch."""
        if self.active_leaf_id is None:
            return []
        branch: list[Node] = []
        current: Node | None = self.nodes[self.active_leaf_id]
        while current is not None:
            branch.append(current)
            current = self.nodes.get(current.parent_id) if current.parent_id else None
        branch.reverse()
        return branch

    def leaves(self) -> list[Node]:
        """Return every leaf node (nodes with no children)."""
        child_ids = {n.parent_id for n in self.nodes.values() if n.parent_id}
        return [n for n in self.nodes.values() if n.id not in child_ids]

    # -- persistence --------------------------------------------------------

    def to_jsonl(self) -> str:
        """Serialize the session as newline-delimited JSON."""
        meta = json.dumps(
            {"session_id": self.id, "active_leaf_id": self.active_leaf_id}
        )
        lines = [meta]
        lines.extend(node.model_dump_json() for node in self.nodes.values())
        return "\n".join(lines) + "\n"

    @classmethod
    def from_jsonl(cls, text: str) -> "Session":
        """Rebuild a session from newline-delimited JSON."""
        session: Session | None = None
        for line in text.splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            if "session_id" in data and "parent_id" not in data:
                session = cls(
                    id=data["session_id"], active_leaf_id=data["active_leaf_id"]
                )
                continue
            if session is None:
                session = cls()
            node = Node.model_validate(data)
            session.nodes[node.id] = node
        if session is None:
            session = cls()
        if session.active_leaf_id is None and session.nodes:
            # No meta header present: the last-appended node is the active leaf.
            session.active_leaf_id = next(reversed(session.nodes))
        return session

    def save(self, path: Path) -> None:
        """Persist the session to ``path`` (parent dirs are created)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Session":
        """Load a session persisted by :meth:`save`."""
        return cls.from_jsonl(Path(path).read_text(encoding="utf-8"))
