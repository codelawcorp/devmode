import os
import sys
import fire
import yaml
from logger import logger
from kubernetes import client, config
import sys
import socket
logger.debug("Setting up logging and attempting to load kubeconfig")
try:
    kubeconfig = os.getenv("KUBECONFIG", "~/.kube/config")
    logger.debug(f"Using kubeconfig path: {kubeconfig}")
    config.load_kube_config(config_file=kubeconfig)
except config.config_exception.ConfigException as e:
    logger.debug(f"Error loading kubeconfig: {e}")
    logger.info(f"Error loading kubeconfig: {e}")
    sys.exit(1)

WORKSPACE_NAME = None

logger.debug("Creating API clients")
V1_API = client.CoreV1Api()
APPS_API = client.AppsV1Api()

def get_deployment_definition(deployment_name, namespace="default"):
    """
    Fetches the deployment definition from the Kubernetes cluster.

    Args:
        deployment_name (str): The name of the deployment.
        namespace (str): The namespace of the deployment (default: "default").

    Returns:
        V1Deployment: Deployment definition.
    """
    logger.debug(f"Getting deployment definition for {deployment_name} in namespace {namespace}")
    try:
        logger.debug("Fetching deployment definition from API")
        deployment = APPS_API.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        return deployment
    except client.exceptions.ApiException as e:
        logger.debug(f"API Exception when fetching deployment: {e}")
        logger.info(f"Error fetching deployment {deployment_name} in namespace {namespace}: {e}")
        return None

def modify_deployment_for_dev_mode(deployment):
    """
    Modifies the deployment definition for development mode:
    - Sets replicas to 1
    - Sets user ID to 0 in containers
    - Changes container command and args
    - Appends "-devmode" to the deployment name

    Args:
        deployment (V1Deployment): The original deployment definition.

    Returns:
        V1Deployment: Modified deployment definition.
    """
    logger.debug("Starting deployment modification for dev mode")
    if not deployment:
        logger.debug("Deployment is None, returning None")
        return None

    logger.debug("Setting replicas to 1")
    deployment.spec.replicas = 1

    logger.debug("Removing resourceVersion and other service fields")
    deployment.metadata.resource_version = None
    deployment.metadata.uid = None
    deployment.metadata.creation_timestamp = None
    deployment.metadata.generation = None
    deployment.metadata.annotations = None
    deployment.metadata.annotations = None
    deployment.metadata.owner_references = None
    deployment.metadata.managed_fields = None

    logger.debug("Modifying containers")
    for container in deployment.spec.template.spec.containers:
        logger.debug(f"Changing `{container.name}` container's command")
        container.command = ["/bin/sh", "-c"]
        logger.debug(f"Changing `{container.name}` container's args")
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

                chmod u+w /root # This is required for vscode server
                sleep infinity
                """]

        logger.debug("Setting security context")
        if not container.security_context:
            container.security_context = client.V1SecurityContext()
        container.security_context.run_as_user = 0

        logger.debug("Removing probes")
        container.liveness_probe = None
        container.readiness_probe = None

        logger.debug("Handling container ports")
        if not container.ports:
            container.ports = [client.V1ContainerPort(container_port=8080)]
        else:
            for port in container.ports:
                if not port.container_port:
                    port.container_port = 8080

    logger.debug("Updating deployment name")
    import socket
    
    deployment.metadata.name += f"-{WORKSPACE_NAME}-devmode"
    deployment.spec.template.metadata.labels["app"] = deployment.metadata.name

    logger.debug("Clearing pod security context")
    deployment.spec.template.spec.security_context = None

    logger.debug("Processing volumes and mounts")
    if deployment.spec.template.spec.volumes:
        volumes_to_keep = []
        for vol in deployment.spec.template.spec.volumes:
            if not (vol.name.startswith("kube-api-access") or vol.name == "eks-pod-identity-token"):
                volumes_to_keep.append(vol)
        deployment.spec.template.spec.volumes = volumes_to_keep

        logger.debug("Removing corresponding volume mounts")
        for container in deployment.spec.template.spec.containers:
            if container.volume_mounts:
                container.volume_mounts = [
                    mount for mount in container.volume_mounts
                    if not (mount.name.startswith("kube-api-access") or mount.name == "eks-pod-identity-token")
                ]

    return deployment

def start(deployment_name, namespace="default", workspace_name = None):
    if workspace_name is None:
        logger.debug("No workspace name provided, using hostname")
        global WORKSPACE_NAME 
        WORKSPACE_NAME = socket.gethostname().lower().replace('_','-')
    """
    Main function to fetch, modify, and create the deployment in dev mode.

    Args:
        deployment_name (str): The name of the deployment.
        namespace (str): The namespace of the deployment (default: "default").
    """
    logger.debug(f"Starting main function with deployment_name={deployment_name}, namespace={namespace}")
    deployment = modify_deployment_for_dev_mode(get_deployment_definition(deployment_name, namespace))
    if deployment:
        logger.debug("Dumping deployment definition to YAML")
        logger.debug(yaml.dump(deployment.to_dict(), default_flow_style=False))

        # Create PVC for the deployment
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=f"{deployment.metadata.name}-pvc",
                namespace=namespace
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(
                    requests={"storage": "2Gi"}
                ),
                storage_class_name="gp2"
            )
        )

        try:
            logger.debug("Creating PVC")
            V1_API.create_namespaced_persistent_volume_claim(
                namespace=namespace,
                body=pvc
            )
            logger.info(f"Created PVC {pvc.metadata.name}")
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.warning(f"PVC {pvc.metadata.name} already exists")
            else:
                logger.error(f"Failed to create PVC: {e}")
                raise

        try:
            logger.debug("Attempting to create deployment in cluster")
            APPS_API.create_namespaced_deployment(
                body=deployment,
                namespace=namespace
            )
            logger.debug("Deployment created successfully")
            logger.info(f"Successfully created deployment {deployment.metadata.name} in namespace {namespace}")
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.error(f"Deployment {deployment.metadata.name} already exists in namespace {namespace}")
                logger.warning("To recreate the deployment, first delete it with:")
                logger.warning(f"kubectl delete deployment {deployment.metadata.name} -n {namespace}")
                exit(1)
            else:
                logger.error(f"Failed to create deployment: {e}")

if __name__ == "__main__":
    logger.debug("Starting script")
    fire.Fire({
        'start': start
    })
