"""Azure management commands - main entry point."""

import click
from .datalake import datalake
from .spn import spn


@click.group()
def azure():
    """Azure management commands"""
    pass


azure.add_command(datalake)
azure.add_command(spn)
