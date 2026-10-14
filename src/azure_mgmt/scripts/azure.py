"""Azure management commands - main entry point."""

import click
import csv
import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import List, Dict
from .datalake import datalake
from .spn import spn
from .oauth import setup_oauth


@click.group()
def azure():
    """Azure management commands"""
    pass


def _json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, Iterable):
        return [_json_value(item) for item in value]
    return str(value)


def _message_text(message) -> str:
    body = message.body
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")


    try:
        chunks = list(body)
    except TypeError:
        return str(body)

    if all(isinstance(chunk, bytes) for chunk in chunks):
        return b"".join(chunks).decode("utf-8", errors="replace")

    body_chunks = "".join(str(chunk) for chunk in chunks) 
    return body_chunks


def _message_attributes(message) -> dict:
    attributes = {}
    for name in dir(message):

        if name.startswith("_"):
            continue

        try:
            value = getattr(message, name)
        except Exception:
            continue
        if not callable(value):
            attributes[name] = _json_value(value)

    attributes["messageText"] = _message_text(message)
    return attributes


def _display_message_text(message_text: str):
    decoded = message_text
    for _ in range(2):
        if not isinstance(decoded, str):
            return decoded
        try:
            decoded = json.loads(decoded)
        except json.JSONDecodeError:
            break

    if not isinstance(decoded, str):
        return decoded



    return " ".join(
        decoded.replace("\\r", " ").replace("\\n", " ").replace("\\t", " ").split()
    )


def _display_attributes(attributes: dict, attribute_names: list[str] | None = None) -> dict:
    if attribute_names is None:
        displayed = {name: value for name, value in attributes.items() if name != "body"}
    else:
        displayed = {name: attributes[name] for name in attribute_names if name in attributes}


    message_in_displayed = [attr for attr in ["messageText", "message", "raw_amqp_message" ] if attr in displayed]
    if message_in_displayed:
        for message_attr in message_in_displayed: 
            displayed[message_attr] = _display_message_text(attributes[message_attr])

    return displayed


def _log_progress(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    click.echo(f"[{timestamp}] {message}", err=True)


def _csv_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _write_csv(path: str, rows: list[dict]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for name in row:
            if name not in fieldnames:
                fieldnames.append(name)

    # utf-8-sig so Excel detects the encoding
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: _csv_value(row.get(name)) for name in fieldnames})


@azure.command("peek-sb-queue")
@click.option(
    "--connection-string",
    envvar="SERVICEBUS_CONNECTION_STR",
    prompt="Service Bus connection string",
    required=True,
    help="Prompt for the Service Bus namespace connection string (or set SERVICEBUS_CONNECTION_STR).",
)
@click.option(
    "--queue-name",
    envvar="SERVICEBUS_QUEUE_NAME",
    required=True,
    help="Queue name (or set SERVICEBUS_QUEUE_NAME).",
)
@click.option("--max-messages", default=200, show_default=True, type=int, help="Maximum messages to peek.")
@click.option("--batch-size", default=100, show_default=True, type=int, help="Messages to request per peek call.")
@click.option(
    "--sequence-number",
    type=click.IntRange(min=0),
    help="Sequence number from which to start peeking (inclusive).",
)
@click.option(
    "--source",
    type=click.Choice(["queue", "dead-letter"], case_sensitive=False),
    default="queue",
    show_default=True,
    help="Message source to peek.",
)
@click.option(
    "--query",
    help="JMESPath expression used to include messages, e.g. contains(messageText, 'error').",
)
@click.option(
    "--attributes",
    help="Comma-separated attributes to show, e.g. sequence_number,enqueued_time_utc,messageText.",
)
@click.option(
    "--csv-output",
    type=click.Path(dir_okay=False, writable=True),
    help="Write matched messages to this CSV file (Excel friendly) instead of printing JSON.",
)
def peek_sb_queue(
    connection_string: str,
    queue_name: str,
    max_messages: int,
    batch_size: int,
    sequence_number: int | None,
    source: str,
    query: str | None,
    attributes: str | None,
    csv_output: str | None,
):
    """Peek Azure Service Bus queue messages without consuming them."""
    if max_messages <= 0:
        raise click.BadParameter("--max-messages must be > 0")
    if batch_size <= 0:
        raise click.BadParameter("--batch-size must be > 0")

    try:
        from azure.servicebus import ServiceBusClient, ServiceBusSubQueue
        import jmespath
    except ImportError as exc:
        raise click.ClickException(
            "Missing dependency. Install it with: pip install azure-servicebus jmespath"
        ) from exc

    try:
        compiled_query = jmespath.compile(query) if query else None
    except jmespath.exceptions.JMESPathError as exc:
        raise click.BadParameter(str(exc), param_hint="--query") from exc

    attribute_names = None
    if attributes is not None:
        attribute_names = [name.strip() for name in attributes.split(",") if name.strip()]
        if not attribute_names:
            raise click.BadParameter("Provide at least one attribute", param_hint="--attributes")

    inspected = 0
    results: list[dict] = []
    sub_queue = ServiceBusSubQueue.DEAD_LETTER if source == "dead-letter" else None

    _log_progress(
        f"Connecting to queue {queue_name!r} (source={source}, max_messages={max_messages}, "
        f"batch_size={batch_size}, sequence_number={sequence_number})"
    )
    with ServiceBusClient.from_connection_string(conn_str=connection_string) as servicebus_client:
        _log_progress("Service Bus client connected; opening receiver")
        with servicebus_client.get_queue_receiver(
            queue_name=queue_name,
            prefetch_count=batch_size,
            sub_queue=sub_queue,
        ) as receiver:
            _log_progress("Receiver opened")
            while inspected < max_messages:
                remaining = max_messages - inspected
                request_size = min(batch_size, remaining)
                _log_progress(
                    f"Peeking up to {request_size} messages from sequence number {sequence_number}"
                )
                peeked_msgs = receiver.peek_messages(
                    max_message_count=request_size,
                    sequence_number=sequence_number,
                )
                _log_progress(f"Peek returned {len(peeked_msgs)} messages")

                if not peeked_msgs:
                    _log_progress("No more messages available")
                    break

                for msg in peeked_msgs:
                    msg_attributes = _message_attributes(msg)
                    if compiled_query is None or compiled_query.search(msg_attributes):
                        results.append(_display_attributes(msg_attributes, attribute_names))

                inspected += len(peeked_msgs)
                sequence_number = peeked_msgs[-1].sequence_number + 1
                _log_progress(
                    f"Batch processed; inspected={inspected}, matched={len(results)}, "
                    f"next_sequence_number={sequence_number}"
                )

    _log_progress(f"Peek done; total inspected={inspected}, matched={len(results)}")

    if csv_output:
        if not results:
            _log_progress("No matched messages; CSV file not written")
            return
        _write_csv(csv_output, results)
        _log_progress(f"Wrote {len(results)} messages to {csv_output}")
        return

    if not results:
        return

    _log_progress("Matched messages:")
    click.echo(json.dumps(results, ensure_ascii=False, indent=2))
azure.add_command(datalake)
azure.add_command(spn)
azure.add_command(setup_oauth)
