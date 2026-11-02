"""Azure AI Search (Cognitive Search) index management commands."""

import copy
import itertools
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

import click

from helpers.azure import resolve_environments, set_subscription
from helpers.ui import banner

DEFAULT_SEARCH_SUFFIX = "search.windows.net"
DEFAULT_BATCH_SIZE = 1000
MAX_BATCH_SIZE = 1000  # Azure AI Search hard limit per indexing request


@click.group()
def search():
    """Azure AI Search management commands"""
    pass


def _log_progress(message: str) -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    click.echo(f"[{timestamp}] {message}", err=True)


def _import_sdk():
    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError as exc:
        raise click.ClickException(
            "Missing dependency. Install it with: pip install azure-search-documents azure-identity"
        ) from exc
    return DefaultAzureCredential, SearchClient, SearchIndexClient, ResourceNotFoundError


def _resolve_endpoint(service: str, suffix: str) -> str:
    """Build a search endpoint from a bare service name, or pass a full URL through as-is."""
    if service.startswith("http://") or service.startswith("https://"):
        return service.rstrip("/")
    return f"https://{service}.{suffix}"



def _strip_search_metadata(document: Dict[str, Any]) -> Dict[str, Any]:
    """Drop the '@search.*' fields (score, highlights, captions, ...) returned by search results."""
    return {key: value for key, value in document.items() if not key.startswith("@search.")}


@search.command("copy-index")
@click.option("--source-service", required=True, help="Source search service name, or a full endpoint URL.")
@click.option("--source-index", required=True, help="Name of the index to copy from the source service.")
@click.option("--target-service", required=True, help="Target search service name, or a full endpoint URL.")
@click.option("--target-index", help="Name of the index to create/overwrite on the target service. Defaults to --source-index.")
@click.option("--foundry-name", help="Name of the foundry service used for vectorization on the target")
@click.option("--search-suffix", default=DEFAULT_SEARCH_SUFFIX, show_default=True, help="Domain suffix used when building endpoints from bare service names (e.g. for sovereign clouds).")
@click.option("--select", help="Comma-separated list of fields to copy. Defaults to all retrievable fields.")
@click.option("--batch-size", default=DEFAULT_BATCH_SIZE, show_default=True, type=click.IntRange(1, MAX_BATCH_SIZE), help="Documents per indexing batch.")
@click.option("--recreate-index", is_flag=True, help="Delete and recreate the target index if it already exists.")
@click.option("--skip-documents", is_flag=True, help="Only copy the index schema, skip copying documents.")
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt when deleting an existing target index.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes.")
def copy_index(
    source_service: str,
    source_index: str,
    target_service: str,
    target_index: Optional[str],
    search_suffix: str,
    select: Optional[str],
    foundry_name: Optional[str],
    batch_size: int,
    recreate_index: bool,
    skip_documents: bool,
    yes: bool,
    dry_run: bool,
):
    """
    Copy an Azure AI Search index (schema + documents) from one search service to another.

    Authenticates with the current Azure AD identity (az login / managed identity /
    environment credentials) via DefaultAzureCredential, so the caller needs the
    "Search Index Data Reader" role on the source service and the
    "Search Service Contributor" + "Search Index Data Contributor" roles on the target.

    Note: this only copies the index definition and its documents. Use copy-all
    (or copy-datasources / copy-skillsets / copy-indexers) for the rest.

    Example:
        mycli azure search copy-index \\
            --source-service pd9021search --source-index products \\
            --target-service td9021search
    """
    target_index = target_index or source_index
    DefaultAzureCredential, SearchClient, SearchIndexClient, ResourceNotFoundError = _import_sdk()

    source_endpoint = _resolve_endpoint(source_service, search_suffix)
    target_endpoint = _resolve_endpoint(target_service, search_suffix)

    banner(
        f"💡 Source: {source_endpoint} / {source_index}\n"
        f"💡 Target: {target_endpoint} / {target_index}",
        fg="cyan",
    )
    if dry_run:
        click.secho("⚠️  === DRY RUN MODE - No changes will be made ===", fg="yellow", bold=True)

    credential = DefaultAzureCredential()
    source_index_client = SearchIndexClient(source_endpoint, credential)
    target_index_client = SearchIndexClient(target_endpoint, credential)

    _log_progress(f"Fetching schema for index '{source_index}' from {source_endpoint}")
    source_definition = source_index_client.get_index(source_index)
    click.echo(f"source index : {source_definition.as_dict()}")

    existing_target = None
    try:

        _log_progress(f"Fetching schema for index '{target_index}' from {target_endpoint}")
        click.echo(f"target index client : {target_index_client}")
        existing_target = target_index_client.get_index(target_index)
    except ResourceNotFoundError:
        pass

    click.echo(f"target index : {existing_target}")

    if existing_target is not None and not recreate_index:
        raise click.ClickException(
            f"Index '{target_index}' already exists on {target_endpoint}. "
            "Pass --recreate-index to delete and recreate it."
        )

    if existing_target is not None and recreate_index:
        if not yes and not dry_run:
            click.confirm(
                f"⚠️  This will delete the existing index '{target_index}' on {target_endpoint} "
                "and all its documents. Continue?",
                abort=True,
            )
        if dry_run:
            click.secho(f"⚠️  [DRY RUN] Would delete existing index '{target_index}'", fg="yellow")
        else:
            _log_progress(f"Deleting existing index '{target_index}' on {target_endpoint}")
            target_index_client.delete_index(target_index)

    target_definition = copy.deepcopy(source_definition)
    target_definition.name = target_index

    
    if foundry_name: 
        vectorizers = target_definition.vector_search.vectorizers

        if len(vectorizers) > 0 : click.secho(f"⚠️ Script excepts just one vectorizers, found {len(vectorizers)}, applying foundry_name to first vectorizer only", fg="yellow")
        if len(vectorizers) <= 0 : raise click.ClickException(f"Script excepts one vectorizer, found 0. Aborting")

        target_vectorizer = vectorizers[0]

        if target_vectorizer.kind != "azureOpenAI": click.ClickException(f"Vectorizer is not an azureOpenAI type vectorizer")

        replacement = f"https://{foundry_name}.openai.azure.com"
        click.secho(f"Replacing target_definition's vectorizer resourceUri : {target_vectorizer.parameters.resource_url} -> {replacement}")

        target_vectorizer.parameters.resource_url = f"https://{foundry_name}.openai.azure.com"


    if getattr(target_definition, "encryption_key", None):
        click.secho(
            "⚠️  Source index uses a customer-managed encryption key tied to its own Key Vault; "
            "dropping it from the target definition (target will use Microsoft-managed keys).",
            fg="yellow",
        )
        target_definition.encryption_key = None

    if dry_run:
        click.secho(f"⚠️  [DRY RUN] Would create/update index '{target_index}' on {target_endpoint}", fg="yellow")
    else:
        _log_progress(f"Creating index '{target_index}' on {target_endpoint}")
        click.echo(f"target_definition: {target_definition}")
        target_index_client.create_or_update_index(target_definition)
        click.secho(f"✅ Index '{target_index}' created on {target_endpoint}", fg="green")

    if skip_documents:
        _log_progress("--skip-documents set; not copying documents")
        return

    source_search_client = SearchClient(source_endpoint, source_index, credential)
    target_search_client = SearchClient(target_endpoint, target_index, credential)
    select_fields = [field.strip() for field in select.split(",") if field.strip()] if select else None

    _log_progress(f"Fetching documents from '{source_index}' (select={select_fields or '*'})")
    results = source_search_client.search(search_text="*", select=select_fields, include_total_count=True)
    total_count = results.get_count()
    _log_progress(f"Source index reports {total_count} document(s) matching the query")

    copied = 0
    failed: List[Dict[str, Any]] = []
    documents = (_strip_search_metadata(document) for document in results)
    while True:
        batch = list(itertools.islice(documents, batch_size))
        if not batch:
            break

        if dry_run:
            click.secho(f"⚠️  [DRY RUN] Would upload {len(batch)} document(s)", fg="yellow")
            copied += len(batch)
            continue

        start = time.monotonic()
        upload_results = target_search_client.upload_documents(batch)
        elapsed = time.monotonic() - start
        batch_failed = [
            {"key": result.key, "error_message": result.error_message}
            for result in upload_results
            if not result.succeeded
        ]
        copied += len(batch) - len(batch_failed)
        failed += batch_failed
        _log_progress(
            f"Uploaded batch of {len(batch)} ({len(batch) - len(batch_failed)} ok, {len(batch_failed)} failed) in {elapsed:.1f}s"
        )

    if not dry_run: _log_progress(f"Done; copied {copied}/{total_count} document(s), {len(failed)} failure(s)")

    if failed:
        for failure in failed[:20]:
            click.secho(f"  ❌ key={failure['key']}: {failure['error_message']}", fg="red", err=True)
        if len(failed) > 20:
            click.secho(f"  ... and {len(failed) - 20} more", fg="red", err=True)
        raise click.ClickException(f"{len(failed)} document(s) failed to copy")

    if dry_run: 
        click.secho(f"⚠️  [DRY RUN] Would Copy {copied} document(s) to '{target_index}' on {target_endpoint}", fg="yellow")
    else: 
        click.secho(f"✅ Copied {copied} document(s) to '{target_index}' on {target_endpoint}", fg="green")


def _import_indexer_sdk():
    try:
        from azure.identity import DefaultAzureCredential
        from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
        from azure.core.exceptions import ResourceNotFoundError
    except ImportError as exc:
        raise click.ClickException(
            "Missing dependency. Install it with: pip install azure-search-documents azure-identity"
        ) from exc
    return DefaultAzureCredential, SearchIndexClient, SearchIndexerClient, ResourceNotFoundError


def _selected(objects, names: Sequence[str]):
    if not names:
        return list(objects)
    missing = set(names) - {obj.name for obj in objects}
    if missing:
        raise click.ClickException(f"Not found on source: {', '.join(sorted(missing))}")
    return [obj for obj in objects if obj.name in names]


def _storage_account_label(connection_string: str) -> str:
    """Return the storage account name without exposing any secret in the string."""
    for part in connection_string.split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "ResourceId":
            return value.rstrip("/").rsplit("/", 1)[-1]
        if key.strip() == "AccountName":
            return value
    return "<unknown>"


def _host(uri: Optional[str]) -> str:
    return urlparse(uri).netloc if uri else "<none>"


def _exists(get_fn, name: str, not_found) -> bool:
    try:
        get_fn(name)
        return True
    except not_found:
        return False


def _copy_datasources(
    source_endpoint: str,
    target_endpoint: str,
    names: Sequence[str],
    target_connection_string: Optional[str],
    overwrite: bool,
    dry_run: bool,
) -> Dict[str, List[str]]:
    DefaultAzureCredential, _, SearchIndexerClient, ResourceNotFoundError = _import_indexer_sdk()
    credential = DefaultAzureCredential()
    source = SearchIndexerClient(source_endpoint, credential)
    target = SearchIndexerClient(target_endpoint, credential)

    summary: Dict[str, List[str]] = {"copied": [], "skipped": [], "failed": []}
    for ds in _selected(source.get_data_source_connections(), names):
        if not overwrite and _exists(target.get_data_source_connection, ds.name, ResourceNotFoundError):
            click.secho(f"⚠️  Data source '{ds.name}' already exists on target; use --overwrite", fg="yellow")
            summary["skipped"].append(ds.name)
            continue

        definition = copy.deepcopy(ds)
        if target_connection_string:
            definition.credentials.connection_string = target_connection_string
        elif not (definition.credentials.connection_string or "").startswith("ResourceId="):
            # The API redacts key/SAS connection strings, so they cannot be copied as-is.
            click.secho(
                f"⚠️  Data source '{ds.name}' uses a key-based connection string; pass --target-connection-string",
                fg="yellow",
            )
            summary["skipped"].append(ds.name)
            continue
        elif not target_connection_string:
            click.secho(
                f"⚠️  Data source '{ds.name}' still points at source storage "
                f"'{_storage_account_label(definition.credentials.connection_string)}'",
                fg="yellow",
            )
        if getattr(definition, "encryption_key", None):
            definition.encryption_key = None

        label = _storage_account_label(definition.credentials.connection_string)
        if dry_run:
            click.secho(f"⚠️  [DRY RUN] Would copy data source '{ds.name}' -> storage '{label}'", fg="yellow")
        else:
            try:
                target.create_or_update_data_source_connection(definition)
                click.secho(f"✅ Data source '{ds.name}' copied (storage '{label}')", fg="green")
            except Exception as exc:  # noqa: BLE001 - report per object and continue
                click.secho(f"❌ Data source '{ds.name}': {exc}", fg="red", err=True)
                summary["failed"].append(ds.name)
                continue
        summary["copied"].append(ds.name)
    return summary


def _copy_skillsets(
    source_endpoint: str,
    target_endpoint: str,
    names: Sequence[str],
    target_foundry_uri: Optional[str],
    overwrite: bool,
    dry_run: bool,
) -> Dict[str, List[str]]:
    DefaultAzureCredential, _, SearchIndexerClient, ResourceNotFoundError = _import_indexer_sdk()
    credential = DefaultAzureCredential()
    source = SearchIndexerClient(source_endpoint, credential)
    target = SearchIndexerClient(target_endpoint, credential)

    summary: Dict[str, List[str]] = {"copied": [], "skipped": [], "failed": []}
    for skillset in _selected(source.get_skillsets(), names):
        if not overwrite and _exists(target.get_skillset, skillset.name, ResourceNotFoundError):
            click.secho(f"⚠️  Skillset '{skillset.name}' already exists on target; use --overwrite", fg="yellow")
            summary["skipped"].append(skillset.name)
            continue

        definition = copy.deepcopy(skillset)
        for skill in definition.skills or []:
            # JSON "resourceUri" is exposed as "resource_url" by the SDK models.
            current = getattr(skill, "resource_url", None)
            if current is None:
                continue
            if target_foundry_uri:
                skill.resource_url = target_foundry_uri
                click.echo(f"  skill '{skill.name}': {_host(current)} -> {_host(target_foundry_uri)}")
            else:
                click.secho(f"⚠️  Skill '{skill.name}' still points at source resource {_host(current)}", fg="yellow")
        if getattr(definition, "encryption_key", None):
            definition.encryption_key = None

        if dry_run:
            click.secho(f"⚠️  [DRY RUN] Would copy skillset '{skillset.name}'", fg="yellow")
        else:
            try:
                target.create_or_update_skillset(definition)
                click.secho(f"✅ Skillset '{skillset.name}' copied", fg="green")
            except Exception as exc:  # noqa: BLE001 - report per object and continue
                click.secho(f"❌ Skillset '{skillset.name}': {exc}", fg="red", err=True)
                summary["failed"].append(skillset.name)
                continue
        summary["copied"].append(skillset.name)
    return summary


def _copy_indexers(
    source_endpoint: str,
    target_endpoint: str,
    names: Sequence[str],
    enable: bool,
    overwrite: bool,
    dry_run: bool,
) -> Dict[str, List[str]]:
    DefaultAzureCredential, SearchIndexClient, SearchIndexerClient, ResourceNotFoundError = _import_indexer_sdk()
    credential = DefaultAzureCredential()
    source = SearchIndexerClient(source_endpoint, credential)
    target = SearchIndexerClient(target_endpoint, credential)
    target_indexes = SearchIndexClient(target_endpoint, credential)

    summary: Dict[str, List[str]] = {"copied": [], "skipped": [], "failed": []}
    for indexer in _selected(source.get_indexers(), names):
        if not overwrite and _exists(target.get_indexer, indexer.name, ResourceNotFoundError):
            click.secho(f"⚠️  Indexer '{indexer.name}' already exists on target; use --overwrite", fg="yellow")
            summary["skipped"].append(indexer.name)
            continue

        # In dry-run, upstream objects may legitimately be missing because they were only simulated.
        missing = []
        if not dry_run:
            if not _exists(target.get_data_source_connection, indexer.data_source_name, ResourceNotFoundError):
                missing.append(f"data source '{indexer.data_source_name}'")
            if not _exists(target_indexes.get_index, indexer.target_index_name, ResourceNotFoundError):
                missing.append(f"index '{indexer.target_index_name}'")
            if indexer.skillset_name and not _exists(target.get_skillset, indexer.skillset_name, ResourceNotFoundError):
                missing.append(f"skillset '{indexer.skillset_name}'")
        if missing:
            click.secho(f"❌ Indexer '{indexer.name}': missing on target: {', '.join(missing)}", fg="red", err=True)
            summary["failed"].append(indexer.name)
            continue

        definition = copy.deepcopy(indexer)
        definition.is_disabled = not enable
        if getattr(definition, "encryption_key", None):
            definition.encryption_key = None

        if dry_run:
            click.secho(f"⚠️  [DRY RUN] Would copy indexer '{indexer.name}' (disabled={not enable})", fg="yellow")
        else:
            try:
                target.create_or_update_indexer(definition)
                click.secho(f"✅ Indexer '{indexer.name}' copied (disabled={not enable})", fg="green")
            except Exception as exc:  # noqa: BLE001 - report per object and continue
                click.secho(f"❌ Indexer '{indexer.name}': {exc}", fg="red", err=True)
                summary["failed"].append(indexer.name)
                continue
        summary["copied"].append(indexer.name)
    return summary


@search.command("copy-datasources")
@click.option("--source-service", required=True, help="Source search service name, or a full endpoint URL.")
@click.option("--target-service", required=True, help="Target search service name, or a full endpoint URL.")
@click.option("--search-suffix", default=DEFAULT_SEARCH_SUFFIX, show_default=True, help="Domain suffix used when building endpoints from bare service names.")
@click.option("--name", "names", multiple=True, help="Only copy the object with this name. Repeatable. Defaults to all.")
@click.option("--overwrite", is_flag=True, help="Update objects that already exist on the target instead of skipping them.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes.")
@click.option("--target-connection-string", help="Replaces credentials.connectionString on every copied data source, e.g. 'ResourceId=/subscriptions/.../storageAccounts/<name>;'.")
def copy_datasources(source_service, target_service, search_suffix, names, overwrite, dry_run, target_connection_string):
    """Copy data sources between search services, optionally pointing them at different storage."""
    source_endpoint = _resolve_endpoint(source_service, search_suffix)
    target_endpoint = _resolve_endpoint(target_service, search_suffix)
    banner(f"💡 Source: {source_endpoint}\n💡 Target: {target_endpoint}", fg="cyan")
    summary = _copy_datasources(source_endpoint, target_endpoint, names, target_connection_string, overwrite, dry_run)
    click.echo(
        f"Data sources: {len(summary['copied'])} copied, {len(summary['skipped'])} skipped, {len(summary['failed'])} failed"
    )
    if summary["failed"]:
        raise click.ClickException(f"{len(summary['failed'])} object(s) failed to copy")


@search.command("copy-skillsets")
@click.option("--source-service", required=True, help="Source search service name, or a full endpoint URL.")
@click.option("--target-service", required=True, help="Target search service name, or a full endpoint URL.")
@click.option("--search-suffix", default=DEFAULT_SEARCH_SUFFIX, show_default=True, help="Domain suffix used when building endpoints from bare service names.")
@click.option("--name", "names", multiple=True, help="Only copy the object with this name. Repeatable. Defaults to all.")
@click.option("--overwrite", is_flag=True, help="Update objects that already exist on the target instead of skipping them.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes.")
@click.option("--target-foundry-uri", help="Replaces resourceUri on every skill that has one, e.g. 'https://<name>.openai.azure.com'.")
def copy_skillsets(source_service, target_service, search_suffix, names, overwrite, dry_run, target_foundry_uri):
    """Copy skillsets between search services, optionally pointing skills at a different Foundry resource."""
    source_endpoint = _resolve_endpoint(source_service, search_suffix)
    target_endpoint = _resolve_endpoint(target_service, search_suffix)
    banner(f"💡 Source: {source_endpoint}\n💡 Target: {target_endpoint}", fg="cyan")
    summary = _copy_skillsets(source_endpoint, target_endpoint, names, target_foundry_uri, overwrite, dry_run)
    click.echo(
        f"Skillsets: {len(summary['copied'])} copied, {len(summary['skipped'])} skipped, {len(summary['failed'])} failed"
    )
    if summary["failed"]:
        raise click.ClickException(f"{len(summary['failed'])} object(s) failed to copy")


@search.command("copy-indexers")
@click.option("--source-service", required=True, help="Source search service name, or a full endpoint URL.")
@click.option("--target-service", required=True, help="Target search service name, or a full endpoint URL.")
@click.option("--search-suffix", default=DEFAULT_SEARCH_SUFFIX, show_default=True, help="Domain suffix used when building endpoints from bare service names.")
@click.option("--name", "names", multiple=True, help="Only copy the object with this name. Repeatable. Defaults to all.")
@click.option("--overwrite", is_flag=True, help="Update objects that already exist on the target instead of skipping them.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes.")
@click.option("--enable", is_flag=True, help="Create indexers enabled. By default they are created disabled.")
def copy_indexers(source_service, target_service, search_suffix, names, overwrite, dry_run, enable):
    """Copy indexers between search services. Their data source, index and skillset must already exist on the target."""
    source_endpoint = _resolve_endpoint(source_service, search_suffix)
    target_endpoint = _resolve_endpoint(target_service, search_suffix)
    banner(f"💡 Source: {source_endpoint}\n💡 Target: {target_endpoint}", fg="cyan")
    summary = _copy_indexers(source_endpoint, target_endpoint, names, enable, overwrite, dry_run)
    click.echo(
        f"Indexers: {len(summary['copied'])} copied, {len(summary['skipped'])} skipped, {len(summary['failed'])} failed"
    )
    if summary["failed"]:
        raise click.ClickException(f"{len(summary['failed'])} object(s) failed to copy")


@search.command("copy-all")
@click.option("--source-service", required=True, help="Source search service name, or a full endpoint URL.")
@click.option("--target-service", required=True, help="Target search service name, or a full endpoint URL.")
@click.option("--search-suffix", default=DEFAULT_SEARCH_SUFFIX, show_default=True, help="Domain suffix used when building endpoints from bare service names.")
@click.option("--overwrite", is_flag=True, help="Update objects that already exist on the target instead of skipping them.")
@click.option("--dry-run", is_flag=True, help="Show what would be done without making changes.")
@click.option("--target-connection-string", help="Replaces credentials.connectionString on every copied data source.")
@click.option("--target-foundry-uri", help="Replaces resourceUri on every skill that has one.")
@click.option("--enable", is_flag=True, help="Create indexers enabled. By default they are created disabled.")
def copy_all(source_service, target_service, search_suffix, overwrite, dry_run, target_connection_string, target_foundry_uri, enable):
    """Copy data sources, then skillsets, then indexers (the order their dependencies require)."""
    source_endpoint = _resolve_endpoint(source_service, search_suffix)
    target_endpoint = _resolve_endpoint(target_service, search_suffix)
    banner(f"💡 Source: {source_endpoint}\n💡 Target: {target_endpoint}", fg="cyan")
    datasources = _copy_datasources(source_endpoint, target_endpoint, (), target_connection_string, overwrite, dry_run)
    skillsets = _copy_skillsets(source_endpoint, target_endpoint, (), target_foundry_uri, overwrite, dry_run)
    indexers = _copy_indexers(source_endpoint, target_endpoint, (), enable, overwrite, dry_run)
    for label, summary in (("Data sources", datasources), ("Skillsets", skillsets), ("Indexers", indexers)):
        click.echo(
            f"{label}: {len(summary['copied'])} copied, {len(summary['skipped'])} skipped, {len(summary['failed'])} failed"
        )
    failed = len(datasources["failed"]) + len(skillsets["failed"]) + len(indexers["failed"])
    if failed:
        raise click.ClickException(f"{failed} object(s) failed to copy")


