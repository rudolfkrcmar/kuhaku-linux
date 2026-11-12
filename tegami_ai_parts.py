"""What the Assistant's parts may rely on: the Assistant (tegami_ai.py) is assembled from
parts kept by subject (tegami_ai_writing.Writing); what they share is declared here once so the
type checker knows it. Nothing here runs."""

from typing import TYPE_CHECKING, Any


class AssistantParts:
    accounts: Any
    busy: Any
    lock: Any
    notify: Any
    store: Any
    wake: Any

    if TYPE_CHECKING:

        def get(self, k: Any, default: Any = ...) -> Any: ...
        def learn_voice(self) -> Any: ...
        def owner(self) -> Any: ...
        def prepare_replies(self) -> Any: ...
        def put(self, k: Any, v: Any) -> Any: ...
