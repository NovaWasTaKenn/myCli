import sys

import click

from config_mgmt.scripts.config import config
from obsidian.scripts.notes import notes
from azure_mgmt.scripts.azure import azure
from azure_mgmt.scripts.aks import aks
from databricks_mgmt.scripts.databricks import databricks
from utils.scripts.alarm import utils


@click.group
def mycli():
    pass


@mycli.command
def test():
    print(sys.path)


mycli.add_command(config)
mycli.add_command(notes)
mycli.add_command(azure)
mycli.add_command(aks)
mycli.add_command(databricks)
mycli.add_command(utils)
