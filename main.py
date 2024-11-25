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
        logger.error(f"Error fetching deployment {deployment_name} in namespace {namespace}: {e}")
        exit(1)


def modify_deployment_for_dev_mode(deployment, workspace_path):
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

    logger.debug("Updating deployment name")
    deployment.metadata.name += f"-{WORKSPACE_NAME}-devmode"
    # deployment.spec.template.metadata.labels["app"] = deployment.metadata.name

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
    logger.debug("Adding PVC volume and mount")
    # Add volume for PVC
    if not deployment.spec.template.spec.volumes:
        deployment.spec.template.spec.volumes = []
    pvc_name = f"{deployment.metadata.name}-pvc"
    deployment.spec.template.spec.volumes.append(
        client.V1Volume(
            name="workspace",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                claim_name=pvc_name
            )
        )
    )

    # Add volume mount to all containers
    for container in deployment.spec.template.spec.containers:
        if not container.volume_mounts:
            container.volume_mounts = []
        container.volume_mounts.append(
            client.V1VolumeMount(
                name="workspace",
                mount_path=workspace_path
            )
        )

    return deployment, pvc_name

def start(deployment_name, namespace="default", workspace_name = None, workspace_path = "/app"):
    if workspace_name is None:
        logger.debug("No workspace name provided, using hostname")
        global WORKSPACE_NAME 
        WORKSPACE_NAME = socket.gethostname().lower().replace('_','-')
    """
    Main function to fetch, modify, and create the deployment in dev mode.

    Args:
        deployment_name (str): The name of the deployment.
        namespace (str): The namespace of the deployment (default: "default").
        workspace_name (str): The name of the workspace (default: hostname).
        workspace_path (str): The path to the workspace, ideally where the code is COPY-ed in Dockerfile (default: "/app").
        
    """
    logger.debug(f"Starting main function with deployment_name={deployment_name}, namespace={namespace}")
    (deployment, pvc_name) = modify_deployment_for_dev_mode(get_deployment_definition(deployment_name, namespace), workspace_path)

    logger.debug("Dumping deployment definition to YAML")
    logger.debug(yaml.dump(deployment.to_dict(), default_flow_style=False))

    
    # Create PVC for the deployment
    pvc = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name=pvc_name,
            namespace=namespace,
            labels={"tool": "devmode"}
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
        pvc_result = V1_API.create_namespaced_persistent_volume_claim(
            namespace=namespace,
            body=pvc
        )
        logger.info(f"Created PVC {pvc.metadata.name}")
    except client.exceptions.ApiException as e:
        if e.status == 409:
            logger.warning(f"PVC {pvc.metadata.name} already exists")
            pvc_result = V1_API.read_namespaced_persistent_volume_claim(
                name=pvc.metadata.name,
                namespace=namespace
            )
            logger.debug(f"Retrieved existing PVC {pvc.metadata.name}")
        else:
            logger.error(f"Failed to create PVC: {e}")
            logger.error("Exiting due to PVC creation failure")
            sys.exit(1)

    try:
        logger.debug("Attempting to create deployment in cluster")
        deployment_result = APPS_API.create_namespaced_deployment(
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
            sys.exit(1)
        else:
            logger.error(f"Failed to create deployment: {e}")
            logger.debug("Attempting to cleanup PVC due to deployment creation failure")
            try:
                V1_API.delete_namespaced_persistent_volume_claim(
                    name=pvc_name,
                    namespace=namespace
                )
                logger.info(f"Cleaned up PVC {pvc_name}")
            except client.exceptions.ApiException as e:
                logger.error(f"Failed to cleanup PVC {pvc_name}: {e}")
            sys.exit(1)

            

    # Create secret to store deployment and PVC UIDs
    secret_name = deployment.metadata.name
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(
            name=secret_name,
            namespace=namespace,
            labels={"tool": "devmode"}
        ),
        string_data={
            "deployment_name": deployment_result.metadata.name, 
            "pvc_name": pvc_result.metadata.name
        }
    )

    try:
        logger.debug("Creating secret to store dependant resource names")
        V1_API.create_namespaced_secret(
            namespace=namespace,
            body=secret
        )
        logger.info(f"Created secret {secret_name}")
    except client.exceptions.ApiException as e:
        if e.status == 409:
            logger.warning(f"Secret {secret_name} already exists")
        else:
            logger.error(f"Failed to create secret: {e}")
            logger.error("Exiting due to secret creation failure") 
            raise
            exit(1)


def list_workspaces():
    """List all devmode workspaces across all namespaces."""
    logger.debug("Listing all devmode workspaces")
    
    try:
        logger.debug("Fetching secrets with tool=devmode label")
        # Get all secrets with tool=devmode label across all namespaces
        secrets = V1_API.list_secret_for_all_namespaces(
            label_selector="tool=devmode"
        )

        logger.debug("Preparing table data")
        # Prepare data for table
        table_data = []
        for secret in secrets.items:
            logger.debug(f"Processing secret {secret.metadata.name}")
            secret_name = secret.metadata.name
            namespace = secret.metadata.namespace
            # Workspace name is same as secret name
            workspace_name = secret_name
            table_data.append([secret_name, namespace, workspace_name])

        logger.debug("Creating table with tabulate")
        # Create and display table using tabulate
        headers = ["Secret Name", "Namespace", "Workspace Name"]
        from tabulate import tabulate
        table = tabulate(
            table_data,
            headers=headers,
            tablefmt="grid"
        )
        
        # Add title and print table
        title = "\nWorkspace List\n"
        logger.info(f"{title}{table}\n")
        
    except client.exceptions.ApiException as e:
        logger.error(f"Failed to list workspaces: {e}")
        sys.exit(1)

def delete_workspace(secret_name, namespace="default"):
    """Delete a devmode workspace and associated resources.
    
    Args:
        secret_name (str): Name of the secret/workspace to delete
        namespace (str): Namespace where the workspace exists (default: default)
    """
    import base64
    logger.debug(f"Deleting workspace {secret_name} in namespace {namespace}")
    
    try:
        logger.debug(f"Reading secret {secret_name}")
        try:
            secret = V1_API.read_namespaced_secret(
                name=secret_name,
                namespace=namespace
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logger.error(f"Secret {secret_name} not found in namespace {namespace}")
                sys.exit(1)
            raise
        
        logger.debug("Extracting resource names from secret")
        # Extract UIDs from secret data
        deployment_name = base64.b64decode(secret.data.get("deployment_name")).decode() if secret.data.get("deployment_name") else None
        pvc_name = base64.b64decode(secret.data.get("pvc_name")).decode() if secret.data.get("pvc_name") else None
        
        if deployment_name:
            logger.debug(f"Attempting to delete deployment {secret_name}")
            # Delete deployment
            try:
                APPS_API.delete_namespaced_deployment(
                    name=deployment_name,
                    namespace=namespace
                )
                logger.info(f"Deleted deployment {deployment_name}")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning(f"Deployment {deployment_name} already deleted")
                else:
                    logger.error(f"Failed to delete deployment: {e}")
                    raise
        
        if pvc_name:
            logger.debug(f"Attempting to delete PVC {secret_name}")
            # Delete PVC
            try:
                V1_API.delete_namespaced_persistent_volume_claim(
                    name=pvc_name,
                    namespace=namespace
                )
                logger.info(f"Deleted PVC {pvc_name}")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning(f"PVC {pvc_name} already deleted")
                else:
                    logger.error(f"Failed to delete PVC: {e}")
                    raise
                
        logger.debug(f"Attempting to delete secret {secret_name}")
        # Delete the secret itself
        try:
            V1_API.delete_namespaced_secret(
                name=secret_name,
                namespace=namespace
            )
            logger.info(f"Deleted secret {secret_name}")
        except client.exceptions.ApiException as e:
            if e.status == 404:
                logger.warning(f"Secret {secret_name} already deleted")
            else:
                logger.error(f"Failed to delete secret: {e}")
                raise
        
    except client.exceptions.ApiException as e:
        logger.error(f"Failed to delete workspace: {e}")
        sys.exit(1)

if __name__ == "__main__":
    logger.debug("Starting script")
    fire.Fire({
        'start': start,
        'list': list_workspaces,
        'delete': delete_workspace,
        # 'update': update_workspace
    })
