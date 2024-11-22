import os
import sys
import fire
import yaml
import logging
from kubernetes import client, config
import sys

logging.debug("Setting up logging and attempting to load kubeconfig")
try:
    kubeconfig = os.getenv("KUBECONFIG", "~/.kube/config")
    logging.debug(f"Using kubeconfig path: {kubeconfig}")
    config.load_kube_config(config_file=kubeconfig)
except config.config_exception.ConfigException as e:
    logging.debug(f"Error loading kubeconfig: {e}")
    print(f"Error loading kubeconfig: {e}")
    sys.exit(1)

logging.debug("Creating CoreV1 API client")
# Create an API client for CoreV1
V1_API = client.CoreV1Api()

# def remove_null_and_empty_fields(obj):
#     """
#     Recursively removes keys with None or empty values from a dictionary.
#     Args:
#         obj: The object to process (dict, list, or other).

#     Returns:
#         A cleaned version of the object.
#     """
#     if isinstance(obj, dict):
#         return {
#             k: remove_null_and_empty_fields(v)
#             for k, v in obj.items()
#             if v not in [None, {}, [], ""]
#         }
#     elif isinstance(obj, list):
#         return [remove_null_and_empty_fields(v) for v in obj if v not in [None, {}, [], ""]]
#     else:
#         return obj


def get_clean_pod_definition(pod_name, namespace="default"):
    """
    Fetches the pod definition from the Kubernetes cluster and removes redundant fields.

    Args:
        pod_name (str): The name of the pod.
        namespace (str): The namespace of the pod (default: "default").

    Returns:
        V1Pod: Cleaned pod definition.
    """
    logging.debug(f"Getting clean pod definition for {pod_name} in namespace {namespace}")
    try:
        # Fetch the pod definition
        logging.debug("Fetching pod definition from API")
        pod = V1_API.read_namespaced_pod(name=pod_name, namespace=namespace)

        logging.debug("Removing redundant metadata fields")
        # Remove redundant metadata fields
        logging.debug("Setting pod.metadata.generate_name to None")
        pod.metadata.generate_name = None
        logging.debug("Setting pod.metadata.managed_fields to None") 
        pod.metadata.managed_fields = None
        logging.debug("Setting pod.metadata.creation_timestamp to None")
        pod.metadata.creation_timestamp = None
        logging.debug("Setting pod.metadata.resource_version to None")
        pod.metadata.resource_version = None
        logging.debug("Setting pod.metadata.uid to None")
        pod.metadata.uid = None
        logging.debug("Setting pod.metadata.self_link to None")
        pod.metadata.self_link = None
        logging.debug("Setting pod.metadata.generation to None")
        pod.metadata.generation = None
        logging.debug("Setting pod.metadata.owner_references to None")
        pod.metadata.owner_references = None

        logging.debug("Processing labels to remove")
        # Remove specific labels
        labels_to_remove = [
            # "app.kubernetes.io/instance", # This one ise used by service selector
            # "app.kubernetes.io/name",  # This one ise used by service selector
            "app.kubernetes.io/managed-by", 
            "app.kubernetes.io/part-of",
            "app.kubernetes.io/version",
            "helm.sh/chart",
            "pod-template-hash"
        ]
        
        if pod.metadata.labels:
            logging.debug("Removing specified labels")
            for label in labels_to_remove:
                logging.debug(f"Removing label: {label}")
                pod.metadata.labels.pop(label, None)

        logging.debug("Clearing pod status")
        # Clear status
        pod.status = None

        return pod
    except client.exceptions.ApiException as e:
        logging.debug(f"API Exception when fetching pod: {e}")
        print(f"Error fetching pod {pod_name} in namespace {namespace}: {e}")
        return None


def modify_pod_for_dev_mode(pod):
    """
    Modifies the pod definition for development mode:
    - Sets user ID to 0.
    - Changes command to "/bin/sh" and args to "sleep infinity".
    - Appends "-devmode" to the pod's name.

    Args:
        pod (V1Pod): The original pod definition.

    Returns:
        V1Pod: Modified pod definition.
    """
    logging.debug("Starting pod modification for dev mode")
    if not pod:
        logging.debug("Pod is None, returning None")
        return None

    logging.debug("Modifying containers")
    # Modify containers
    for container in pod.spec.containers:
        logging.debug(f"Changing `{container.name}` container's command")
        container.command = ["/bin/sh", "-c"]
        logging.debug(f"Changing `{container.name}` container's args")
        container.args = ["""
                if command -v apt-get &> /dev/null; then
                    apt-get update && apt-get install -y git
                elif command -v yum &> /dev/null; then
                    yum install -y git
                elif command -v dnf &> /dev/null; then
                    dnf install -y git
                elif command -v zypper &> /dev/null; then
                    zypper install -y git
                elif command -v pacman &> /dev/null; then
                    pacman -Sy git
                elif command -v apk &> /dev/null; then
                    apk add git
                else
                    echo "Unsupported package manager. Please install git manually."
                fi
                sleep infinity
                """]

        logging.debug("Setting security context")
        # Set user ID to 0
        if not container.security_context:
            container.security_context = client.V1SecurityContext()
        container.security_context.run_as_user = 0

        logging.debug("Removing probes")
        # Remove probes
        container.liveness_probe = None
        container.readiness_probe = None

        logging.debug("Handling container ports")
        # Handle ports
        if not container.ports:
            container.ports = [client.V1ContainerPort(container_port=8080)]
        else:
            for port in container.ports:
                if not port.container_port:
                    port.container_port = 8080

    logging.debug("Updating pod name")
    # Append "-devmode" to pod name
    pod.metadata.name += "-devmode"

    logging.debug("Clearing pod security context")
    pod.spec.security_context = None

    logging.debug("Processing volumes and mounts")
    # Remove specific volumes and their mounts
    if pod.spec.volumes:
        volumes_to_keep = []
        for vol in pod.spec.volumes:
            if not (vol.name.startswith("kube-api-access") or vol.name == "eks-pod-identity-token"):
                volumes_to_keep.append(vol)
        pod.spec.volumes = volumes_to_keep

        logging.debug("Removing corresponding volume mounts")
        # Remove corresponding volume mounts
        for container in pod.spec.containers:
            if container.volume_mounts:
                container.volume_mounts = [
                    mount for mount in container.volume_mounts
                    if not (mount.name.startswith("kube-api-access") or mount.name == "eks-pod-identity-token")
                ]

    logging.debug("Removing node name")
    # Remove node_name
    pod.spec.node_name = None

    return pod


def main(pod_name, namespace="default"):
    """
    Main function to fetch, modify, and print the pod definition in YAML format.

    Args:
        pod_name (str): The name of the pod.
        namespace (str): The namespace of the pod (default: "default").
    """
    logging.debug(f"Starting main function with pod_name={pod_name}, namespace={namespace}")
    pod = modify_pod_for_dev_mode(get_clean_pod_definition(pod_name, namespace))
    if pod:
        logging.debug("Dumping pod definition to YAML")
        print(yaml.dump(pod.to_dict(), default_flow_style=False))
        try:
            logging.debug("Attempting to create pod in cluster")
            V1_API.create_namespaced_pod(
                body=pod,
                namespace=namespace
            )
            logging.debug("Pod created successfully")
            print(f"Successfully created pod {pod.metadata.name} in namespace {namespace}")
        except client.exceptions.ApiException as e:
            logging.debug(f"Failed to create pod: {e}")
            print(f"Failed to create pod: {e}")


if __name__ == "__main__":
    logging.debug("Starting script")
    fire.Fire(main)
