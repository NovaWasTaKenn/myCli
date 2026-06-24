"""Utility functions for Azure management commands."""

import yaml
from typing import List, Dict, Any


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
