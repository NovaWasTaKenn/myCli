import sys

import click

from config_mgmt.scripts.config import config
from obsidian.scripts.notes import notes
from azure_mgmt.scripts.azure import azure


@click.group
def mycli():
    pass


@mycli.command
def test():
    print(sys.path)


mycli.add_command(config)
mycli.add_command(notes)
mycli.add_command(azure)
