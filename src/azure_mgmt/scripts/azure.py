import click
import subprocess
import json
import yaml
import os
from pathlib import Path
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


@click.group()
def azure():
    """Azure management commands"""
    pass


@azure.command()
@click.argument("keyvault_name", type=str)
@click.option("--tenant-id", required=True, help="Azure tenant ID")
@click.option("--identity-id", help="User assigned managed identity ID (auto-fetched from AKS if not provided)")
@click.option("--aks-code", help="AKS project code")
@click.option("--aks-env", help="AKS environment")
@click.option("--namespace", default="default", help="Kubernetes namespace for the SecretProviderClass")
@click.option("--secret-name", help="Name for the Kubernetes secret (defaults to <keyvault>-secret)")
@click.option("--output", "-o", type=click.Path(), help="Output file path (defaults to stdout)")
@click.option("--secrets", multiple=True, help="Specific secrets to include (if not specified, all secrets will be included)")
def generate_spc(
    keyvault_name: str,
    tenant_id: str,
    identity_id: str,
    aks_code: str,
    aks_env: str,
    namespace: str,
    secret_name: str,
    output: str,
    secrets: tuple
):
    """
    Generate a SecretProviderClass YAML from Azure Key Vault secrets.
    
    This command reads secrets from an Azure Key Vault and generates a 
    SecretProviderClass manifest for the Secrets Store CSI Driver.
    
    Example:
        mycli azure generate-spc rd91381akv01 \\
            --tenant-id ebaeb74c-eeae-40f8-b755-10b0e2bf7528 \\
            --identity-id c66c99b0-c213-4f6f-aac2-3a358f1bf262 \\
            --namespace cfm \\
            --output spc.yaml
    """
    aks_code = "3221" if not aks_code else aks_code
    resource_group = f"r400{aks_env}d9{aks_code}"
    kube_cluster = f"{aks_env}d9{aks_code}aks01"
    try:
        # Fetch identity_id from AKS cluster if not provided
        if not identity_id:
            if not resource_group or not kube_cluster:
                click.echo("Error: Either --identity-id must be provided, or both --resource-group and --kube-cluster must be specified.", err=True)
                raise click.Abort()
            
            click.echo(f"Fetching identity ID from AKS cluster: {kube_cluster}...")
            result = subprocess.run(
                ["az", "aks", "show", 
                 "--resource-group", resource_group,
                 "--name", kube_cluster,
                 "--query", "addonProfiles.azureKeyvaultSecretsProvider.identity.clientId",
                 "-o", "tsv"],
                capture_output=True,
                text=True,
                check=True
            )
            identity_id = result.stdout.strip()
            
            if not identity_id:
                click.echo("Error: Could not fetch identity ID from AKS cluster. Make sure the Azure Key Vault Secrets Provider addon is enabled.", err=True)
                raise click.Abort()
            
            click.echo(f"Using identity ID: {identity_id}")
        
        # Get list of secrets from Azure Key Vault using Azure CLI
        click.echo(f"Fetching secrets from Key Vault: {keyvault_name}...")
        
        result = subprocess.run(
            ["az", "keyvault", "secret", "list", "--vault-name", keyvault_name, "--query", "[].name", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        
        all_secrets = json.loads(result.stdout)
        click.echo(f"all_secrets : {all_secrets}")
        
        # Filter secrets if specific ones were requested
        if secrets:
            secret_list = [s for s in all_secrets if s in secrets]
            if len(secret_list) != len(secrets):
                missing = set(secrets) - set(secret_list)
                click.echo(f"Warning: The following secrets were not found: {', '.join(missing)}", err=True)
        else:
            secret_list = all_secrets
        
        if not secret_list:
            click.echo("No secrets found in the Key Vault.", err=True)
            return
        
        click.echo(f"Found {len(secret_list)} secret(s)")
        
        # Generate SecretProviderClass
        if not secret_name:
            secret_name = f"{keyvault_name}-secret"
        
        spc_name = f"az-{keyvault_name}-spc"
        
        # Build secretObjects data
        secret_objects_data = []
        for secret in secret_list:
            # Convert secret name to uppercase with underscores for env var style
            env_var_name = secret.upper().replace("-", "_")
            secret_objects_data.append({
                "objectName": secret,
                "key": env_var_name
            })
        
        # Build parameters objects array
        objects_array = []
        for secret in secret_list:
            objects_array.append({
                "objectName": secret,
                "objectType": "secret",
                "objectVersion": ""
            })
        
        # Construct the SecretProviderClass
        spc = {
            "apiVersion": "secrets-store.csi.x-k8s.io/v1",
            "kind": "SecretProviderClass",
            "metadata": {
                "name": spc_name,
                "namespace": namespace
            },
            "spec": {
                "provider": "azure",
                "secretObjects": [
                    {
                        "secretName": secret_name,
                        "type": "Opaque",
                        "data": secret_objects_data
                    }
                ],
                "parameters": {
                    "keyvaultName": keyvault_name,
                    "objects": format_objects_array(objects_array),
                    "tenantId": tenant_id,
                    "usePodIdentity": "false",
                    "useVMManagedIdentity": "true",
                    "userAssignedIdentityID": identity_id
                }
            }
        }
        
        # Convert to YAML
        yaml_output = yaml.dump(spc, default_flow_style=False, sort_keys=False, width=4096, indent=2)
        
        # Output to file or stdout
        if output:

            output = Path(output)
            # Check if output is a directory, if so append the filename
            if len(output.suffix) < 1:
                click.echo(f"is dir {spc_name}")

                output = output / f"{spc_name}.yaml"
            
            output = output.expanduser()
            click.echo(f"output path : {output}")

            if not output.parent.exists():
                click.echo(f"Creating dir : {output.parent}")
                output.parent.mkdir()

            with open(output, 'w') as f:
                f.write(yaml_output)
            click.echo(f"SecretProviderClass written to: {output}")
        else:
            click.echo("\n" + yaml_output)
        
    except subprocess.CalledProcessError as e:
        click.echo(f"Error executing Azure CLI command: {e.stderr}", err=True)
        click.echo("Make sure you're logged in with 'az login' and have access to the Key Vault.", err=True)
        raise click.Abort()
    except json.JSONDecodeError as e:
        click.echo(f"Error parsing Azure CLI output: {e}", err=True)
        raise click.Abort()
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        raise click.Abort()


def format_objects_array(objects: List[Dict[str, Any]]) -> LiteralString:
    """
    Format the objects array in the specific multi-line string format required by SecretProviderClass.
    """
    lines = ["array:"]
    for obj in objects:
        lines.append("  - |")
        lines.append(f'    objectName: {obj["objectName"]}')
        lines.append(f'    objectType: {obj["objectType"]}')
        lines.append(f'    objectVersion: "{obj["objectVersion"]}"')
    return LiteralString("\n".join(lines) + "\n")


# @azure.command()
# @click.argument("keyvault_name", type=str)
# def list_secrets(keyvault_name: str):
#     """
#     List all secrets in an Azure Key Vault.
    
#     Example:
#         mycli azure list-secrets rd91381akv01
#     """
#     try:
#         click.echo(f"Fetching secrets from Key Vault: {keyvault_name}...")
        
#         result = subprocess.run(
#             ["az", "keyvault", "secret", "list", "--vault-name", keyvault_name, "--query", "[].{name:name, enabled:attributes.enabled}", "-o", "table"],
#             capture_output=True,
#             text=True,
#             check=True
#         )
        
#         click.echo(result.stdout)
        
#     except subprocess.CalledProcessError as e:
#         click.echo(f"Error executing Azure CLI command: {e.stderr}", err=True)
#         click.echo("Make sure you're logged in with 'az login' and have access to the Key Vault.", err=True)
#         raise click.Abort()
