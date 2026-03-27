from typing import Optional, Dict, Any, List, Tuple, Callable
import json
import os
import random
import time
import requests
from mudae.core import latency as Latency

DEFAULT_TIMEOUT_SEC = 12.0
MAX_LIMIT = 100


def get_timeout() -> float:
    value = os.getenv('MUDAE_HTTP_TIMEOUT')
    if not value:
        return DEFAULT_TIMEOUT_SEC
    try:
        return float(value)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC


def _sanitize_limit(limit: int) -> int:
    try:
        limit_val = int(limit)
    except (TypeError, ValueError):
        return 50
    return max(1, min(limit_val, MAX_LIMIT))


def _parse_retry_after(response: Optional[requests.Response]) -> Optional[float]:
    if response is None or response.status_code != 429:
        return None
    header_value = (
        response.headers.get('Retry-After')
        or response.headers.get('X-RateLimit-Reset-After')
    )
    if header_value:
        try:
            return float(header_value)
        except (TypeError, ValueError):
            pass
    try:
        payload = response.json()
        retry_after = payload.get('retry_after')
        if retry_after is not None:
            return float(retry_after)
    except Exception:
        pass
    return None


def fetch_messages(
    url: str,
    auth: Dict[str, str],
    after_id: Optional[str] = None,
    limit: int = 50,
    timeout: Optional[float] = None
) -> Tuple[Optional[requests.Response], List[Dict[str, Any]]]:
    params: Dict[str, Any] = {'limit': _sanitize_limit(limit)}
    if after_id:
        params['after'] = str(after_id)
    timeout_sec = get_timeout() if timeout is None else timeout
    try:
        response = requests.get(url, headers=auth, params=params, timeout=timeout_sec)
    except requests.RequestException:
        return None, []
    try:
        data = response.json()
    except Exception:
        data = []
    if not isinstance(data, list):
        data = []
    return response, data


def get_latest_message_id(messages: List[Dict[str, Any]]) -> Optional[str]:
    if not messages:
        return None
    message_id = messages[0].get('id')
    return str(message_id) if message_id else None


def extract_interaction_user_id(message: Dict[str, Any]) -> Optional[str]:
    for key in ('interaction', 'interaction_metadata'):
        interaction = message.get(key)
        if isinstance(interaction, dict):
            user = interaction.get('user')
            if isinstance(user, dict):
                user_id = user.get('id')
                if user_id:
                    return str(user_id)
    return None


def extract_interaction_user_name(message: Dict[str, Any]) -> Optional[str]:
    for key in ('interaction', 'interaction_metadata'):
        interaction = message.get(key)
        if isinstance(interaction, dict):
            user = interaction.get('user')
            if isinstance(user, dict):
                name = user.get('username') or user.get('global_name')
                if name:
                    return str(name)
    return None



def extract_interaction_id(message: Dict[str, Any]) -> Optional[str]:
    """Return interaction id from message['interaction'].id or message['interaction_metadata'].id."""
    for key in ('interaction', 'interaction_metadata'):
        interaction = message.get(key)
        if isinstance(interaction, dict):
            interaction_id = interaction.get('id')
            if interaction_id:
                return str(interaction_id)
    return None

def extract_interaction_name(message: Dict[str, Any]) -> Optional[str]:
    for key in ('interaction', 'interaction_metadata'):
        interaction = message.get(key)
        if isinstance(interaction, dict):
            name = interaction.get('name')
            if name:
                return str(name)
    return None


def filter_messages(
    messages: List[Dict[str, Any]],
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    command_name: Optional[str] = None,
    interaction_id: Optional[str] = None,
    author_id: Optional[str] = None,
    require_interaction: bool = False
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if require_interaction and not (
            isinstance(message.get('interaction'), dict) or isinstance(message.get('interaction_metadata'), dict)
        ):
            continue
        if author_id:
            author = message.get('author', {})
            if not isinstance(author, dict) or str(author.get('id', '')) != str(author_id):
                continue
        if user_id:
            msg_user_id = extract_interaction_user_id(message)
            if msg_user_id != user_id:
                continue
        if user_name:
            msg_user_name = extract_interaction_user_name(message)
            if not msg_user_name or msg_user_name.lower() != user_name.lower():
                continue
        if interaction_id:
            msg_interaction_id = extract_interaction_id(message)
            if msg_interaction_id != interaction_id:
                continue
        if command_name:
            msg_command = extract_interaction_name(message)
            if msg_command != command_name:
                continue
        results.append(message)
    return results


def _find_soft_interaction_matches(
    messages: List[Dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    command_name: Optional[str] = None,
    interaction_id: Optional[str] = None,
    require_interaction: bool = False,
) -> List[Dict[str, Any]]:
    if interaction_id and (user_id or user_name):
        return filter_messages(
            messages,
            user_id=user_id,
            user_name=user_name,
            command_name=command_name,
            require_interaction=require_interaction,
        )
    if interaction_id and not (user_id or user_name):
        return filter_messages(
            messages,
            interaction_id=interaction_id,
            command_name=command_name,
            require_interaction=require_interaction,
        )
    return []


def _find_relevant_interaction_messages(
    messages: List[Dict[str, Any]],
    *,
    command_name: Optional[str] = None,
    require_interaction: bool = False,
) -> List[Dict[str, Any]]:
    if command_name:
        command_matches = filter_messages(
            messages,
            command_name=command_name,
            require_interaction=require_interaction,
        )
        if command_matches:
            return command_matches
    if require_interaction:
        interaction_messages: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            if isinstance(message.get('interaction'), dict) or isinstance(message.get('interaction_metadata'), dict):
                interaction_messages.append(message)
        return interaction_messages
    return []


def _adaptive_delay(attempt_index: int, max_delay: float) -> float:
    if max_delay <= 0:
        return 0.0
    base_delay = min(max_delay, 0.2)
    delay = min(max_delay, base_delay * (2 ** attempt_index))
    jitter = random.uniform(0.85, 1.15)
    return min(max_delay, delay * jitter)


def _resolve_delay_schedule(
    attempts: int,
    delay_sec: float,
    delay_schedule: Optional[List[float]],
) -> Optional[List[float]]:
    return Latency.get_delay_schedule(delay_sec, max(1, int(attempts)), delay_schedule=delay_schedule)


def _attempt_delay(
    attempt_index: int,
    delay_sec: float,
    schedule: Optional[List[float]],
) -> float:
    if schedule:
        if attempt_index < len(schedule):
            return max(0.0, float(schedule[attempt_index]))
        return max(0.0, float(schedule[-1]))
    return _adaptive_delay(attempt_index, delay_sec)


def _notify_poll_result(
    *,
    response: Optional[requests.Response],
    retry_after: Optional[float],
    error: bool,
    attempt_index: int,
    latency_context: Optional[str],
    on_poll_result: Optional[Callable[[Dict[str, Any]], None]],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    status_code: Optional[int] = None
    if response is not None:
        status_code = response.status_code

    Latency.record_poll_result(
        status_code,
        error=error,
        retry_after=retry_after,
        context=latency_context,
    )

    if on_poll_result is None:
        return
    payload: Dict[str, Any] = {
        "attempt": int(attempt_index),
        "status_code": status_code,
        "retry_after": retry_after,
        "error": bool(error),
        "latency_context": latency_context,
    }
    if metadata:
        payload.update(metadata)
    try:
        on_poll_result(payload)
    except Exception:
        return

def _sleep_with_stop(delay_sec: float, stop_check: Optional[Callable[[], bool]]) -> bool:
    if delay_sec <= 0:
        return False
    end_time = time.time() + delay_sec
    while time.time() < end_time:
        if stop_check and stop_check():
            return True
        time.sleep(min(0.05, end_time - time.time()))
    return False

def wait_for_interaction_message(
    url: str,
    auth: Dict[str, str],
    after_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    command_name: Optional[str] = None,
    interaction_id: Optional[str] = None,
    attempts: int = 4,
    delay_sec: float = 1.0,
    delay_schedule: Optional[List[float]] = None,
    latency_context: Optional[str] = None,
    on_poll_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    limit: int = 50,
    stop_check: Optional[Callable[[], bool]] = None
) -> Tuple[Optional[requests.Response], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    last_response: Optional[requests.Response] = None
    last_messages: List[Dict[str, Any]] = []
    best_response: Optional[requests.Response] = None
    best_observed_messages: List[Dict[str, Any]] = []
    best_soft_candidate: Optional[Dict[str, Any]] = None
    best_score = 0
    base_after = after_id
    schedule = _resolve_delay_schedule(attempts, delay_sec, delay_schedule)
    effective_attempts = max(max(1, attempts), len(schedule) if schedule else 0)
    for attempt_index in range(effective_attempts):
        if stop_check and stop_check():
            observed = best_observed_messages if best_observed_messages else last_messages
            return best_response or last_response, observed, best_soft_candidate
        response, messages = fetch_messages(url, auth, after_id=base_after, limit=limit)
        last_response = response
        last_messages = messages
        retry_after: Optional[float] = None
        if response is None:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=True,
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
                metadata={"message_count": 0, "exact_match_count": 0, "soft_match_count": 0},
            )
            if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
                observed = best_observed_messages if best_observed_messages else last_messages
                return best_response or last_response, observed, best_soft_candidate
            continue
        matches = filter_messages(
            messages,
            user_id=user_id,
            user_name=user_name,
            command_name=command_name,
            interaction_id=interaction_id,
            require_interaction=True
        )
        if not matches and interaction_id:
            has_any_interaction_id = any(extract_interaction_id(msg) for msg in messages)
            if not has_any_interaction_id:
                matches = filter_messages(
                    messages,
                    user_id=user_id,
                    user_name=user_name,
                    command_name=command_name,
                    require_interaction=True
                )
        soft_matches: List[Dict[str, Any]] = []
        if not matches:
            soft_matches = _find_soft_interaction_matches(
                messages,
                user_id=user_id,
                user_name=user_name,
                command_name=command_name,
                interaction_id=interaction_id,
                require_interaction=True,
            )
        relevant_messages = _find_relevant_interaction_messages(
            messages,
            command_name=command_name,
            require_interaction=True,
        )
        score = 0
        if soft_matches:
            score = 3
        elif relevant_messages:
            score = 2
        if score > best_score:
            best_score = score
            best_response = response
            best_observed_messages = list(messages)
            best_soft_candidate = soft_matches[0] if soft_matches else best_soft_candidate
        if matches:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=False,
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
                metadata={
                    "message_count": len(messages),
                    "exact_match_count": len(matches),
                    "soft_match_count": len(soft_matches),
                },
            )
            return response, messages, matches[0]
        newest = get_latest_message_id(messages)
        if newest and newest != base_after:
            base_after = newest
        retry_after = _parse_retry_after(response)
        _notify_poll_result(
            response=response,
            retry_after=retry_after,
            error=False,
            attempt_index=attempt_index,
            latency_context=latency_context,
            on_poll_result=on_poll_result,
            metadata={
                "message_count": len(messages),
                "exact_match_count": 0,
                "soft_match_count": len(soft_matches),
                "used_best_observed_batch": bool(best_observed_messages),
            },
        )
        if retry_after is not None:
            wait_delay = max(retry_after, _attempt_delay(attempt_index, delay_sec, schedule))
            if _sleep_with_stop(wait_delay, stop_check):
                observed = best_observed_messages if best_observed_messages else last_messages
                return best_response or last_response, observed, best_soft_candidate
            continue
        if response.status_code >= 500:
            if _sleep_with_stop(max(_attempt_delay(attempt_index, delay_sec, schedule), 1.0), stop_check):
                observed = best_observed_messages if best_observed_messages else last_messages
                return best_response or last_response, observed, best_soft_candidate
            continue
        if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
            observed = best_observed_messages if best_observed_messages else last_messages
            return best_response or last_response, observed, best_soft_candidate
    observed = best_observed_messages if best_observed_messages else last_messages
    return best_response or last_response, observed, best_soft_candidate


def wait_for_author_message(
    url: str,
    auth: Dict[str, str],
    author_id: str,
    after_id: Optional[str] = None,
    attempts: int = 4,
    delay_sec: float = 1.0,
    delay_schedule: Optional[List[float]] = None,
    latency_context: Optional[str] = None,
    on_poll_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    limit: int = 50,
    content_contains: Optional[str] = None,
    stop_check: Optional[Callable[[], bool]] = None
) -> Tuple[Optional[requests.Response], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    last_response: Optional[requests.Response] = None
    last_messages: List[Dict[str, Any]] = []
    base_after = after_id
    schedule = _resolve_delay_schedule(attempts, delay_sec, delay_schedule)
    effective_attempts = max(max(1, attempts), len(schedule) if schedule else 0)
    for attempt_index in range(effective_attempts):
        if stop_check and stop_check():
            return last_response, last_messages, None
        response, messages = fetch_messages(url, auth, after_id=base_after, limit=limit)
        last_response = response
        last_messages = messages
        retry_after: Optional[float] = None
        if response is None:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=True,
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
            )
            if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
                return last_response, last_messages, None
            continue
        candidates = filter_messages(messages, author_id=author_id)
        if content_contains:
            candidates = [msg for msg in candidates if content_contains in msg.get('content', '')]
        if candidates:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=False,
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
            )
            return response, messages, candidates[0]
        newest = get_latest_message_id(messages)
        if newest and newest != base_after:
            base_after = newest
        retry_after = _parse_retry_after(response)
        _notify_poll_result(
            response=response,
            retry_after=retry_after,
            error=False,
            attempt_index=attempt_index,
            latency_context=latency_context,
            on_poll_result=on_poll_result,
        )
        if retry_after is not None:
            wait_delay = max(retry_after, _attempt_delay(attempt_index, delay_sec, schedule))
            if _sleep_with_stop(wait_delay, stop_check):
                return last_response, last_messages, None
            continue
        if response.status_code >= 500:
            if _sleep_with_stop(max(_attempt_delay(attempt_index, delay_sec, schedule), 1.0), stop_check):
                return last_response, last_messages, None
            continue
        if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
            return last_response, last_messages, None
    return last_response, last_messages, None


def _message_hash(message: Dict[str, Any]) -> str:
    try:
        payload = {
            "content": message.get("content", ""),
            "components": message.get("components", []),
            "embeds": message.get("embeds", []),
            "edited_timestamp": message.get("edited_timestamp"),
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(message.get("id", ""))


def fetch_message_by_id(
    base_url: str,
    auth: Dict[str, str],
    channel_id: str,
    message_id: str,
    timeout: Optional[float] = None
) -> Tuple[Optional[requests.Response], Optional[Dict[str, Any]]]:
    if not base_url:
        return None, None
    url = f"{base_url}/channels/{channel_id}/messages/{message_id}"
    timeout_sec = get_timeout() if timeout is None else timeout
    try:
        response = requests.get(url, headers=auth, timeout=timeout_sec)
    except requests.RequestException:
        return None, None
    try:
        data = response.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = None
    return response, data


def wait_for_message_update(
    base_url: str,
    auth: Dict[str, str],
    channel_id: str,
    message_id: str,
    prev_hash: Optional[str] = None,
    attempts: int = 6,
    delay_sec: float = 0.5,
    delay_schedule: Optional[List[float]] = None,
    latency_context: Optional[str] = None,
    on_poll_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    timeout: Optional[float] = None,
    stop_check: Optional[Callable[[], bool]] = None
) -> Tuple[Optional[requests.Response], Optional[Dict[str, Any]], Optional[str]]:
    last_response: Optional[requests.Response] = None
    last_message: Optional[Dict[str, Any]] = None
    last_hash = prev_hash
    schedule = _resolve_delay_schedule(attempts, delay_sec, delay_schedule)
    effective_attempts = max(max(1, attempts), len(schedule) if schedule else 0)
    for attempt_index in range(effective_attempts):
        if stop_check and stop_check():
            return last_response, last_message, last_hash

        response, message = fetch_message_by_id(
            base_url,
            auth,
            channel_id,
            message_id,
            timeout=timeout,
        )
        last_response = response
        last_message = message
        retry_after: Optional[float] = None

        if response is None or message is None:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=(response is None),
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
            )
            if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
                return last_response, last_message, last_hash
            continue

        new_hash = _message_hash(message)
        if prev_hash is None or new_hash != prev_hash:
            _notify_poll_result(
                response=response,
                retry_after=None,
                error=False,
                attempt_index=attempt_index,
                latency_context=latency_context,
                on_poll_result=on_poll_result,
            )
            return response, message, new_hash
        last_hash = new_hash

        retry_after = _parse_retry_after(response)
        _notify_poll_result(
            response=response,
            retry_after=retry_after,
            error=False,
            attempt_index=attempt_index,
            latency_context=latency_context,
            on_poll_result=on_poll_result,
        )
        if retry_after is not None:
            wait_delay = max(retry_after, _attempt_delay(attempt_index, delay_sec, schedule))
            if _sleep_with_stop(wait_delay, stop_check):
                return last_response, last_message, last_hash
            continue
        if response.status_code >= 500:
            if _sleep_with_stop(max(_attempt_delay(attempt_index, delay_sec, schedule), 1.0), stop_check):
                return last_response, last_message, last_hash
            continue
        if _sleep_with_stop(_attempt_delay(attempt_index, delay_sec, schedule), stop_check):
            return last_response, last_message, last_hash

    return last_response, last_message, last_hash
