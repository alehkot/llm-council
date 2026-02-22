"""JSON-based storage for conversations."""

import json
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from pathlib import Path
from .config import DATA_DIR


def _migrate_stage3(stage3: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Migrate old stage3 format to new multi-version format.

    Old format: { model: "...", response: "..." }
    New format: { current: {..., mode: "...", timestamp: "..."}, versions: [...] }
    """
    if stage3 is None:
        return None

    # Already in new format
    if "current" in stage3 and "versions" in stage3:
        return stage3

    # Migrate from old format
    timestamp = datetime.utcnow().isoformat()
    # Preserve existing mode if present, otherwise default to best_of_breed
    mode = stage3.get("mode", "best_of_breed")

    version_entry = {
        "model": stage3.get("model"),
        "response": stage3.get("response"),
        "mode": mode,
        "timestamp": timestamp,
    }

    return {
        "current": version_entry,
        "versions": [version_entry],
    }


def ensure_data_dir():
    """Ensure the data directory exists."""
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)


def get_conversation_path(conversation_id: str) -> str:
    """Get the file path for a conversation."""
    return os.path.join(DATA_DIR, f"{conversation_id}.json")


def create_conversation(conversation_id: str) -> Dict[str, Any]:
    """
    Create a new conversation.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        New conversation dict
    """
    ensure_data_dir()

    conversation = {
        "id": conversation_id,
        "created_at": datetime.utcnow().isoformat(),
        "title": "New Conversation",
        "messages": []
    }

    # Save to file
    path = get_conversation_path(conversation_id)
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)

    return conversation


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    """
    Load a conversation from storage.

    Automatically migrates stage3 data to the new multi-version format.

    Args:
        conversation_id: Unique identifier for the conversation

    Returns:
        Conversation dict or None if not found or corrupted
    """
    path = get_conversation_path(conversation_id)

    try:
        with open(path, 'r') as f:
            conversation = json.load(f)

        # Migrate stage3 format for all assistant messages
        migrated = False
        for message in conversation.get("messages", []):
            if message.get("role") == "assistant" and message.get("stage3"):
                original = message["stage3"]
                # Check if migration is needed (old format lacks 'current' key)
                if "current" not in original:
                    message["stage3"] = _migrate_stage3(original)
                    migrated = True

        # Persist migration if any changes were made
        if migrated:
            save_conversation(conversation)

        return conversation
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        print(f"Error: Corrupted conversation file {conversation_id}: {e}")
        return None
    except IOError as e:
        print(f"Error: Could not read conversation {conversation_id}: {e}")
        return None


def save_conversation(conversation: Dict[str, Any]):
    """
    Save a conversation to storage.

    Args:
        conversation: Conversation dict to save
    """
    ensure_data_dir()

    path = get_conversation_path(conversation['id'])
    with open(path, 'w') as f:
        json.dump(conversation, f, indent=2)


def list_conversations() -> List[Dict[str, Any]]:
    """
    List all conversations (metadata only).

    Returns:
        List of conversation metadata dicts
    """
    ensure_data_dir()

    conversations = []
    for filename in os.listdir(DATA_DIR):
        if filename.endswith('.json'):
            path = os.path.join(DATA_DIR, filename)
            try:
                with open(path, 'r') as f:
                    data = json.load(f)
                    # Return metadata only
                    conversations.append({
                        "id": data["id"],
                        "created_at": data["created_at"],
                        "title": data.get("title", "New Conversation"),
                        "message_count": len(data["messages"])
                    })
            except (json.JSONDecodeError, KeyError, IOError) as e:
                # Skip corrupted files but log the error
                print(f"Warning: Skipping corrupted conversation file {filename}: {e}")
                continue

    # Sort by creation time, newest first
    conversations.sort(key=lambda x: x["created_at"], reverse=True)

    return conversations


def add_user_message(conversation_id: str, content: str):
    """
    Add a user message to a conversation.

    Args:
        conversation_id: Conversation identifier
        content: User message content
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "user",
        "content": content
    })

    save_conversation(conversation)


def add_assistant_message(
    conversation_id: str,
    stage1: List[Dict[str, Any]],
    negative: Optional[Dict[str, Any]],
    stage2: List[Dict[str, Any]],
    stage3: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    trace: Optional[Dict[str, Any]] = None,
):
    """
    Add an assistant message with all 3 stages to a conversation.

    Args:
        conversation_id: Conversation identifier
        stage1: List of individual model responses
        negative: Negative mode context/results if available
        stage2: List of model rankings
        stage3: Final synthesized response
        metadata: Additional metadata (e.g., label mappings, aggregates)
        trace: Debug trace payload for the full flow
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "assistant",
        "stage1": stage1,
        "negative": negative,
        "stage2": stage2,
        "stage3": stage3,
        "metadata": metadata or {},
        "trace": trace,
    })

    save_conversation(conversation)


def add_assistant_placeholder(
    conversation_id: str,
    metadata: Optional[Dict[str, Any]] = None,
    trace: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Append a placeholder assistant message for incremental updates.

    Returns:
        Index of the new assistant message in the conversation.
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["messages"].append({
        "role": "assistant",
        "stage1": None,
        "negative": None,
        "stage2": None,
        "stage3": None,
        "metadata": metadata or {},
        "trace": trace,
    })

    save_conversation(conversation)
    return len(conversation["messages"]) - 1


def update_assistant_message(
    conversation_id: str,
    message_index: int,
    updates: Dict[str, Any],
) -> None:
    """
    Update a previously saved assistant message by index.

    Args:
        conversation_id: Conversation identifier
        message_index: Index of the assistant message to update
        updates: Dict of fields to update (metadata merges with existing)
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    if message_index < 0 or message_index >= len(conversation["messages"]):
        raise IndexError("Assistant message index out of range")

    message = conversation["messages"][message_index]
    if message.get("role") != "assistant":
        raise ValueError("Target message is not an assistant message")

    metadata_update = updates.pop("metadata", None)
    for key, value in updates.items():
        message[key] = value
    if metadata_update is not None:
        message["metadata"] = {
            **(message.get("metadata") or {}),
            **metadata_update,
        }

    save_conversation(conversation)


def update_conversation_title(conversation_id: str, title: str):
    """
    Update the title of a conversation.

    Args:
        conversation_id: Conversation identifier
        title: New title for the conversation
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    conversation["title"] = title
    save_conversation(conversation)


def append_stage3_version(
    conversation_id: str,
    message_index: int,
    version: Dict[str, Any],
    set_as_current: bool = True,
) -> int:
    """
    Append a new synthesis version to an assistant message's stage3.

    Args:
        conversation_id: Conversation identifier
        message_index: Index of the assistant message in the messages array
        version: The version dict with mode, model, response, timestamp
        set_as_current: Whether to set this version as the current one

    Returns:
        Index of the new version in the versions array
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    if message_index < 0 or message_index >= len(conversation["messages"]):
        raise IndexError("Message index out of range")

    message = conversation["messages"][message_index]
    if message.get("role") != "assistant":
        raise ValueError("Target message is not an assistant message")

    stage3 = message.get("stage3")
    if stage3 is None:
        raise ValueError("Message has no stage3 data")

    # Ensure stage3 is in new format
    if "current" not in stage3:
        stage3 = _migrate_stage3(stage3)
        message["stage3"] = stage3

    # Append the new version
    stage3["versions"].append(version)
    version_index = len(stage3["versions"]) - 1

    if set_as_current:
        stage3["current"] = version

    save_conversation(conversation)
    return version_index


def set_stage3_current_version(
    conversation_id: str,
    message_index: int,
    version_index: int,
) -> None:
    """
    Set a specific version as the current stage3 version.

    Args:
        conversation_id: Conversation identifier
        message_index: Index of the assistant message
        version_index: Index of the version to set as current
    """
    conversation = get_conversation(conversation_id)
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")

    if message_index < 0 or message_index >= len(conversation["messages"]):
        raise IndexError("Message index out of range")

    message = conversation["messages"][message_index]
    if message.get("role") != "assistant":
        raise ValueError("Target message is not an assistant message")

    stage3 = message.get("stage3")
    if stage3 is None or "versions" not in stage3:
        raise ValueError("Message has no stage3 versions")

    if version_index < 0 or version_index >= len(stage3["versions"]):
        raise IndexError("Version index out of range")

    stage3["current"] = stage3["versions"][version_index]
    save_conversation(conversation)


_TITLE_SUFFIX_PATTERN = re.compile(r"^(.*?)(?:\s\((\d+)\))?$")


def ensure_unique_title(title: str, conversation_id: Optional[str] = None) -> str:
    """
    Ensure a conversation title is unique by appending an incrementing suffix if needed.

    Args:
        title: Proposed title.
        conversation_id: Optional conversation to exclude from uniqueness checks.

    Returns:
        Unique title.
    """
    base_title = title.strip() or "New Conversation"
    existing_titles = set()
    for conversation in list_conversations():
        if conversation_id and conversation.get("id") == conversation_id:
            continue
        existing_title = (conversation.get("title") or "").strip().lower()
        if existing_title:
            existing_titles.add(existing_title)

    if base_title.lower() not in existing_titles:
        return base_title

    match = _TITLE_SUFFIX_PATTERN.match(base_title)
    root_title = match.group(1).strip() if match else base_title
    if not root_title:
        root_title = "New Conversation"

    suffix = 2
    while True:
        candidate = f"{root_title} ({suffix})"
        if candidate.lower() not in existing_titles:
            return candidate
        suffix += 1


def clear_conversations() -> int:
    """
    Delete all stored conversation JSON files.

    Returns:
        Number of files deleted.
    """
    data_path = Path(DATA_DIR)
    if not data_path.exists():
        return 0

    deleted = 0
    for file_path in data_path.glob("*.json"):
        try:
            file_path.unlink()
            deleted += 1
        except OSError:
            continue

    return deleted


def delete_conversation(conversation_id: str) -> bool:
    """
    Delete a single conversation JSON file.

    Args:
        conversation_id: Conversation identifier

    Returns:
        True if deleted, False if not found or deletion failed.
    """
    path = Path(get_conversation_path(conversation_id))
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        print(f"Error: Could not delete conversation {conversation_id}: {e}")
        return False
