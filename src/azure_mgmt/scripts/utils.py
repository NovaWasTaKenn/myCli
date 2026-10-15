"""Utility functions for Azure management commands."""

import click
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional


class LiteralString(str):
    """Custom string class to preserve literal block scalar format in YAML"""
    pass


def literal_presenter(dumper, data):
    """YAML representer for literal block scalars with clip chomping"""
    # Ensure the string ends with exactly one newline for '|' instead of '|-'
    if not data.endswith('\n'):
        data = data + '\n'
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style='|')


yaml.add_representer(LiteralString, literal_presenter)


def format_objects_array(objects: List[Dict[str, Any]]) -> LiteralString:
    """
    Format the objects array in the specific multi-line string format required by SecretProviderClass.
    """
    lines = ["array:"]
    for obj in objects:
        lines.append("  - |")
        lines.append(f'    objectName: {obj["objectName"]}')
        if "objectAlias" in obj:
            lines.append(f'    objectAlias: {obj["objectAlias"]}')
        lines.append(f'    objectType: {obj["objectType"]}')
        lines.append(f'    objectVersion: "{obj["objectVersion"]}"')
    return LiteralString("\n".join(lines) + "\n")


def dump_manifests(manifests: List[Dict[str, Any]]) -> str:
    """Serialize Kubernetes manifests as a multi-document YAML string."""
    return "---\n".join(
        yaml.dump(manifest, default_flow_style=False, sort_keys=False, width=4096, indent=2)
        for manifest in manifests
    )


def write_output(content: str, output: Optional[str], default_filename: str, label: str) -> None:
    """Write content to output (a file, or default_filename inside a directory), or stdout if None."""
    if not output:
        click.echo("\n" + content)
        return
    output_path = Path(output).expanduser()
    if output_path.is_dir() or not output_path.suffix:
        output_path = output_path / default_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content)
    click.echo(f"✅ {label} written to: {output_path}")
