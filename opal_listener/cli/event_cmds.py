"""opal listen event: publish messages onto the ZeroMQ event bus."""

from __future__ import annotations

import time

import typer

from opal_listener.cli.client import CliError
from opal_listener.cli.output import done, state

event_app = typer.Typer(help="Publish events onto the ZeroMQ event bus.", no_args_is_help=True)

# Give the PUB socket's TCP connection and subscription handshake time to settle before the
# first send; otherwise this short-lived process risks the classic ZeroMQ "slow joiner" problem
# of the very first message being silently dropped.
_CONNECT_SETTLE_SECONDS = 0.2


@event_app.command("publish")
def event_publish(
    ctx: typer.Context,
    topic: str = typer.Argument(..., help="Topic name"),
    message: str = typer.Argument(..., help="Event/message payload"),
) -> None:
    """Publish MESSAGE under TOPIC to the server's --xpub endpoint."""
    try:
        import zmq
    except ImportError as exc:
        raise CliError("pyzmq is required for 'event publish' (pip install pyzmq)") from exc

    xpub_url = state(ctx).xpub_url
    context = zmq.Context()
    socket = context.socket(zmq.PUB)
    try:
        try:
            socket.connect(xpub_url)
        except zmq.ZMQError as exc:
            raise CliError(f"cannot connect to {xpub_url} ({exc})") from exc
        time.sleep(_CONNECT_SETTLE_SECONDS)
        socket.send_multipart([topic.encode("utf-8"), message.encode("utf-8")])
    finally:
        socket.close(linger=0)
        context.term()
    done(ctx, f"published to '{topic}' via {xpub_url}", {"topic": topic, "message": message, "xpub": xpub_url})
