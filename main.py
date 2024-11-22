import os
import fire
import yaml
from kubernetes import client, config


kubeconfig = os.getenv("KUBECONFIG", "~/.kube/config")
config.load_kube_config(config_file=kubeconfig)

# Create an API client for CoreV1
V1_API = client.CoreV1Api()

def remove_null_and_empty_fields(obj):
    """
    Recursively removes keys with None or empty values from a dictionary.
    Args:
        obj: The object to process (dict, list, or other).

    Returns:
        A cleaned version of the object.
    """
    if isinstance(obj, dict):
        return {
            k: remove_null_and_empty_fields(v)
            for k, v in obj.items()
            if v not in [None, {}, [], ""]
        }
    elif isinstance(obj, list):
        return [remove_null_and_empty_fields(v) for v in obj if v not in [None, {}, [], ""]]
    else:
        return obj


def get_clean_pod_definition(pod_name, namespace="default"):
    """
    Fetches the pod definition from the Kubernetes cluster and removes redundant fields.

    Args:
        pod_name (str): The name of the pod.
        namespace (str): The namespace of the pod (default: "default").

    Returns:
        dict: Cleaned pod definition.
    """
    # Load the Kubernetes configuration from KUBECONFIG or the default path


    try:
        # Fetch the pod definition
        pod = V1_API.read_namespaced_pod(name=pod_name, namespace=namespace)

        # Convert the pod object to a dictionary
        pod_dict = pod.to_dict()

        # Remove redundant fields
        for field in [
            "status",
            "metadata.generate_name",
            "metadata.managed_fields",
            "metadata.creation_timestamp",
            "metadata.resource_version",
            "metadata.uid",
            "metadata.self_link",
            "metadata.generation",
            "metadata.owner_references",
        ]:
            keys = field.split(".")
            d = pod_dict
            for key in keys[:-1]:
                d = d.get(key, {})
            if keys[-1] in d:
                d.pop(keys[-1])

        # Remove specific labels
        labels_to_remove = [
            "app.kubernetes.io/instance",
            "app.kubernetes.io/managed-by",
            "app.kubernetes.io/name",
            "app.kubernetes.io/part-of", 
            "app.kubernetes.io/version",
            "helm.sh/chart",
            "pod-template-hash"
        ]
        
        if "metadata" in pod_dict and "labels" in pod_dict["metadata"]:
            for label in labels_to_remove:
                pod_dict["metadata"]["labels"].pop(label, None)

        # Remove null and empty fields
        return remove_null_and_empty_fields(pod_dict)
    except client.exceptions.ApiException as e:
        print(f"Error fetching pod {pod_name} in namespace {namespace}: {e}")
        return {}


def modify_pod_for_dev_mode(pod_dict):
    """
    Modifies the pod definition for development mode:
    - Sets user ID to 0.
    - Changes command to "/bin/sh" and args to "sleep infinity".
    - Appends "-devmode" to the pod's name.

    Args:
        pod_dict (dict): The original pod definition.

    Returns:
        dict: Modified pod definition.
    """
    # Modify containers
    for container in pod_dict.get("spec", {}).get("containers", []):
        container["command"] = ["/bin/sh", "-c"]
        container["args"] = ["sleep infinity"]

        # Set user ID to 0
        if "security_context" not in container:
            container["security_context"] = {}
        container["security_context"]["run_as_user"] = 0

    # Append "-devmode" to pod name
    if "metadata" in pod_dict and "name" in pod_dict["metadata"]:
        pod_dict["metadata"]["name"] += "-devmode"

    # Remove liveness and readiness probes
    for container in pod_dict.get("spec", {}).get("containers", []):
        if "liveness_probe" in container:
            container.pop("liveness_probe")
        if "readiness_probe" in container:
            container.pop("readiness_probe")
    # Remove specific volumes and their corresponding volume mounts
    if "spec" in pod_dict and "volumes" in pod_dict["spec"]:
        # Get list of volumes to remove
        volumes_to_remove = [
            vol["name"] for vol in pod_dict["spec"]["volumes"]
            if vol["name"].startswith("kube-api-access") or vol["name"] == "eks-pod-identity-token"
        ]
        
        # Filter volumes
        pod_dict["spec"]["volumes"] = [
            vol for vol in pod_dict["spec"]["volumes"]
            if vol["name"] not in volumes_to_remove
        ]
        
        # Remove corresponding volume mounts from all containers
        for container in pod_dict.get("spec", {}).get("containers", []):
            if "volume_mounts" in container:
                container["volume_mounts"] = [
                    mount for mount in container["volume_mounts"]
                    if mount["name"] not in volumes_to_remove
                ]

    # Preserve container ports from original configuration
    for container in pod_dict.get("spec", {}).get("containers", []):
        if "ports" not in container:
            # If no ports defined, add default port 8080
            container["ports"] = [{"containerPort": 8080}]
        else:
            # Ensure each port has containerPort specified
            for port in container["ports"]:
                if "containerPort" not in port:
                    port["containerPort"] = 8080


    # # Remove corresponding volume mounts from containers
    # for container in pod_dict.get("spec", {}).get("containers", []):
    #     if "volume_mounts" in container:
    #         container["volume_mounts"] = [
    #             mount for mount in container["volume_mounts"]
    #             if not (mount["name"].startswith("kube-api-access") or mount["name"] == "eks-pod-identity-token")
    #         ]

    # Remove node_name if present
    if "spec" in pod_dict and "node_name" in pod_dict["spec"]:
        pod_dict["spec"].pop("node_name")

    return pod_dict


def main(pod_name, namespace="default"):
    """
    Main function to fetch, modify, and print the pod definition in YAML format.

    Args:
        pod_name (str): The name of the pod.
        namespace (str): The namespace of the pod (default: "default").
        dev_mode (bool): Whether to modify the pod for development mode (default: False).
    """
    pod_definition = modify_pod_for_dev_mode(get_clean_pod_definition(pod_name, namespace))
    # Apply the modified pod definition
    print(yaml.dump(pod_definition, default_flow_style=False))
    try:
        V1_API.create_namespaced_pod(
            body=pod_definition,
            namespace=namespace
        )
        print(f"Successfully created pod {pod_definition['metadata']['name']} in namespace {namespace}")
    except client.exceptions.ApiException as e:
        print(f"Failed to create pod: {e}")



if __name__ == "__main__":
    fire.Fire(main)
