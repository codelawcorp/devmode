#! /usr/bin/env python3
import os

import fire
import yaml
from logger import logger
from kubernetes import client, config
import base64


class Config:
    V1_API = None
    APPS_API = None
    NETWORKING_API = None

    # @staticmethod
    def setup_kubernetes_client():
        logger.debug("Setting up logging and attempting to load kubeconfig")
        try:
            kubeconfig = os.getenv("KUBECONFIG", "~/.kube/config")
            logger.debug(f"Using kubeconfig path: {kubeconfig}")
            config.load_kube_config(config_file=kubeconfig)

            Config.V1_API = client.CoreV1Api()
            Config.NETWORKING_API = client.NetworkingV1Api()
            Config.V1_API = client.CoreV1Api()
            Config.APPS_API = client.AppsV1Api()

            try:
                # Test access to the cluster
                Config.V1_API.list_namespace()
                logger.debug("Successfully loaded kubeconfig")
            except Exception as e:
                logger.error(f"Failed to access cluster: {e}")
                # sys.exit(1)
        except config.config_exception.ConfigException as e:
            logger.error(f"Error loading kubeconfig: {e}")
            # sys.exit(1)


class Workspace:
    """
    A class to manage Kubernetes workloads in development mode.

    Attributes:
        workspace_name (str): The name of the workspace.
        namespace (str): The namespace of the deployment.
        original_deployment_name (str): The original name of the deployment.
        workspace_path (str): The path to the workspace.

    Methods:
        start():
            Creates a copy of the specified deployment with elevated permissions and a PVC.
        delete():
            Deletes a devmode workspace and associated resources.
        recreate(new_workspace_path=None):
        _modify_deployment_for_dev_mode(deployment, workspace_path):
            Modifies the deployment definition for development mode.
        list_workspaces(namespace=None):
            Lists all devmode workspaces across all namespaces.
    """

    def __init__(self, workspace_name, namespace, deployment_name, workspace_path=None):
        self.workspace_name = workspace_name
        self.prefix = f"devmode-{workspace_name}"
        self.namespace = namespace
        self.original_deployment_name = deployment_name
        self.deployment_name = f"{self.original_deployment_name}-{self.prefix}"
        self.pvc_name = f"{self.original_deployment_name}-{self.prefix}"
        self.secret_name = f"{self.original_deployment_name}-{self.prefix}"
        self.service_name = None
        self.ingress_name = None
        self.workspace_path = workspace_path or "/app"
        self.labels = {
            "app.kubernetes.io/instance": self.original_deployment_name,
            "tool": "devmode",
            "workspace": self.workspace_name,
            "tags.datadoghq.com/env": "dev",
            "tags.datadoghq.com/service": self.deployment_name,
            "tags.datadoghq.com/version": "latest",
        }
        self.annotations = {
            "com.datadoghq.ad.tags": f'["workspace_name:{workspace_name}",]'
        }

        Config.setup_kubernetes_client()

    def start(self):
        """Start a devmode workspace.

        This command creates a new Kubernetes deployment with a Persistent Volume Claim (PVC)
        attached and elevated privileges for development purposes.
        """
        original_deployment_raw = self._get_deployment_definition(
            self.original_deployment_name, self.namespace
        )
        deployment_raw = self._modify_deployment_for_dev_mode(
            original_deployment_raw, self.workspace_path
        )

        logger.debug("Dumping deployment definition to YAML")
        logger.debug(yaml.dump(deployment_raw.to_dict(), default_flow_style=False))

        # Create PVC for the deployment
        pvc = client.V1PersistentVolumeClaim(
            metadata=client.V1ObjectMeta(
                name=self.pvc_name, namespace=self.namespace, labels={"tool": "devmode"}
            ),
            spec=client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=client.V1ResourceRequirements(requests={"storage": "2Gi"}),
                storage_class_name="gp2",
            ),
        )

        try:
            logger.debug("Creating PVC")
            pvc_result = Config.V1_API.create_namespaced_persistent_volume_claim(
                namespace=self.namespace, body=pvc
            )
            self.pvc_name = pvc_result.metadata.name
            logger.info(f"Created PVC {pvc.metadata.name}")
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.warning(f"PVC {pvc.metadata.name} already exists")
                pvc_result = Config.V1_API.read_namespaced_persistent_volume_claim(
                    name=pvc.metadata.name, namespace=self.namespace
                )
                logger.debug(f"Retrieved existing PVC {pvc.metadata.name}")
            else:
                logger.error(f"Failed to create PVC: {e}")
                logger.error("Exiting due to PVC creation failure")
                # sys.exit(1)

        # Create deployment in cluster
        try:
            logger.debug("Attempting to create deployment in cluster")
            deployment_result = Config.APPS_API.create_namespaced_deployment(
                body=deployment_raw, namespace=self.namespace
            )
            logger.debug("Deployment created successfully")
            self.deployment_name = deployment_result.metadata.name
            logger.info(
                f"Successfully created deployment {self.deployment_name} in namespace {self.namespace}"
            )
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.error(
                    f"Deployment {self.deployment_name} already exists in namespace {self.namespace}"
                )
                logger.warning("To recreate the deployment, first delete it with:")
                logger.warning(
                    f"kubectl delete deployment {self.deployment_name} -n {self.namespace}"
                )
                # sys.exit(1)
            else:
                logger.error(f"Failed to create deployment: {e}")
                logger.debug(
                    "Attempting to cleanup PVC due to deployment creation failure"
                )
                try:
                    Config.V1_API.delete_namespaced_persistent_volume_claim(
                        name=self.pvc_name, namespace=self.namespace
                    )
                    logger.info(f"Cleaned up PVC {self.pvc_name}")
                except client.exceptions.ApiException as e:
                    logger.error(f"Failed to cleanup PVC {self.pvc_name}: {e}")
                # sys.exit(1)

        service_list = Config.V1_API.list_namespaced_service(
            namespace=self.namespace,
            label_selector=f"app.kubernetes.io/instance={original_deployment_raw.metadata.labels['app.kubernetes.io/instance']}",
        )
        if service_list.items:
            original_service = service_list.items[0]
            self.service_name = f"{original_service.metadata.name}-{self.prefix}"

            # Create a copy of the service with new labels and name
            new_service = original_service
            new_service.metadata.name = self.service_name
            new_service.metadata.labels = self.labels
            new_service.metadata.resource_version = None
            new_service.spec.selector = self.labels
            new_service.spec.cluster_ip = None
            new_service.spec.cluster_i_ps = None  # This is a bug in the k8s client

            try:
                logger.debug(f"Creating service {self.service_name}")
                Config.V1_API.create_namespaced_service(
                    namespace=self.namespace, body=new_service
                )
                logger.info(f"Created service {self.service_name}")
            except client.exceptions.ApiException as e:
                if e.status == 409:
                    logger.warning(f"Service {self.service_name} already exists")
                else:
                    logger.error(f"Failed to create service: {e}")
                    raise
        else:
            logger.warning(
                f"No service found with label app.kubernetes.io/instance={self.original_deployment_name}"
            )

        # Get ingresses in the namespace
        ingress_list = Config.NETWORKING_API.list_namespaced_ingress(
            namespace=self.namespace,
            label_selector=f"app.kubernetes.io/instance={original_deployment_raw.metadata.labels['app.kubernetes.io/instance']}",
        )
        if ingress_list.items:
            original_ingress = ingress_list.items[0]
            self.ingress_name = f"{original_ingress.metadata.name}-{self.prefix}"

            # Create a copy of the ingress with new labels and name
            new_ingress = original_ingress
            new_ingress.metadata.name = self.ingress_name
            new_ingress.metadata.labels = self.labels
            new_ingress.metadata.resource_version = None
            new_ingress.spec.rules[0].host = (
                f"{self.prefix}.{original_ingress.spec.rules[0].host}"
            )

            try:
                logger.debug(f"Creating ingress {self.ingress_name}")
                Config.NETWORKING_API.create_namespaced_ingress(
                    namespace=self.namespace, body=new_ingress
                )
                logger.info(f"Created ingress {self.ingress_name}")
            except client.exceptions.ApiException as e:
                if e.status == 409:
                    logger.warning(f"Ingress {self.ingress_name} already exists")
                else:
                    logger.error(f"Failed to create ingress: {e}")
                    raise

        # except client.exceptions.ApiException as e:
        #     logger.warning(f"Service {self.original_deployment_name} not found: {e}")

        # TODO / make a function of this
        # Create secret to store deployment and PVC UIDs
        secret_name = self.secret_name
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(
                name=secret_name, namespace=self.namespace, labels=self.labels
            ),
            string_data={
                "workspace_name": self.workspace_name,
                "deployment_name": self.deployment_name,
                "original_deployment_name": self.original_deployment_name,
                "pvc_name": self.pvc_name,
                "workspace_path": self.workspace_path,
                "service_name": self.service_name,
                "ingress_name": self.ingress_name,
            },
        )

        try:
            logger.debug("Creating secret to store dependant resource names")
            secret_result = Config.V1_API.create_namespaced_secret(
                namespace=self.namespace, body=secret
            )
            self.secret_name = secret_result.metadata.name
            logger.info(f"Created secret {secret_name}")
        except client.exceptions.ApiException as e:
            if e.status == 409:
                logger.warning(f"Secret {secret_name} already exists")
            else:
                logger.error(f"Failed to create secret: {e}")
                logger.error("Exiting due to secret creation failure")
                raise
                # exit(1)

        return (
            self.workspace_name,
            self.deployment_name,
            self.pvc_name,
            self.service_name,
            self.ingress_name,
            self.secret_name,
        )

    def delete(self):
        """Delete a devmode workspace and associated resources.

        Args:
            self.workspace_name (str): Name of the secret/workspace to delete
            namespace (str): Namespace where the workspace exists (default: default)
        """
        logger.debug(
            f"Deleting workspace {self.workspace_name} in namespace {self.namespace}"
        )

        try:

            if self.deployment_name:
                logger.debug(f"Attempting to delete deployment {self.deployment_name}")
                # Delete deployment
                try:
                    Config.APPS_API.delete_namespaced_deployment(
                        name=self.deployment_name, namespace=self.namespace
                    )
                    logger.info(f"Deleted deployment {self.deployment_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(
                            f"Deployment {self.deployment_name} already deleted"
                        )
                    else:
                        logger.error(f"Failed to delete deployment: {e}")
                        raise

            if self.pvc_name:
                logger.debug(f"Attempting to delete PVC {self.pvc_name}")
                # Delete PVC
                try:
                    Config.V1_API.delete_namespaced_persistent_volume_claim(
                        name=self.pvc_name, namespace=self.namespace
                    )
                    logger.info(f"Deleted PVC {self.pvc_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(f"PVC {self.pvc_name} already deleted")
                    else:
                        logger.error(f"Failed to delete PVC: {e}")
                        raise

            logger.debug(f"Attempting to delete secret {self.secret_name}")

            if self.service_name:
                logger.debug(f"Attempting to delete service {self.service_name}")
                try:
                    Config.V1_API.delete_namespaced_service(
                        name=self.service_name, namespace=self.namespace
                    )
                    logger.info(f"Deleted service {self.service_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(f"Service {self.service_name} already deleted")
                    else:
                        logger.error(f"Failed to delete service: {e}")
                        raise
            # Delete the secret itself
            try:
                Config.V1_API.delete_namespaced_secret(
                    name=self.secret_name, namespace=self.namespace
                )
                logger.info(f"Deleted secret {self.secret_name}")
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning(f"Secret {self.secret_name} already deleted")
                else:
                    logger.error(f"Failed to delete secret: {e}")
                    raise

        except client.exceptions.ApiException as e:
            logger.error(f"Failed to delete workspace: {e}")
            # sys.exit(1)

    def recreate(self, new_workspace_path=None):
        """
        Recreates a workspace by deleting the existing one and starting a new one.

        Args:
            self.workspace_name (str): The name of the workspace to recreate.
            namespace (str): The namespace of the workspace (default: "default").
        """
        logger.debug(
            f"Attempting to recreate workspace {self.workspace_name} in namespace {self.namespace}"
        )

        # Get original deployment name from secret before deletion
        try:
            (
                deployment_name,
                original_deployment_name,
                pvc_name,
                workspace_path,
                service_name,
                ingress_name,
                secret_name,
            ) = self._get_workspace_from_secret(self.workspace_name, self.namespace)
            logger.debug(
                f"Retrieved original deployment name from secret: {original_deployment_name}"
            )
        except client.exceptions.ApiException as e:
            logger.error(
                f"Failed to get secret for workspace {self.workspace_name}: {e}"
            )
            # sys.exit(1)

        # Delete existing workspace
        self.delete()

        # TODO / wait for pvc to be deleted completely

        # Start new workspace
        self.workspace_path = new_workspace_path or workspace_path
        self.start()

        logger.info(f"Successfully recreated workspace {self.workspace_name}")

    def _modify_deployment_for_dev_mode(self, old_deployment, workspace_path):
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
        new_deployment = old_deployment

        logger.debug(f"Updating deployment name with {self.deployment_name}")
        new_deployment.metadata.name = self.deployment_name

        logger.debug("Setting replicas to 1")
        new_deployment.spec.replicas = 1

        new_deployment.metadata.labels = self.labels
        new_deployment.spec.template.metadata.labels = self.labels
        new_deployment.spec.selector.match_labels = self.labels

        logger.debug("Removing resourceVersion and other service fields")
        new_deployment.metadata.resource_version = None
        new_deployment.metadata.uid = None
        new_deployment.metadata.creation_timestamp = None
        new_deployment.metadata.generation = None
        new_deployment.metadata.annotations = self.annotations
        new_deployment.metadata.owner_references = None
        new_deployment.metadata.managed_fields = None

        # TODO / remove podAntiAffinity

        logger.debug("Modifying containers")
        for container in new_deployment.spec.template.spec.containers:
            logger.debug(f"Changing `{container.name}` container's command")
            container.command = ["/bin/sh", "-c"]
            logger.debug(f"Changing `{container.name}` container's args")
            container.args = [
                """
                    if command -v apt-get &> /dev/null; then
                        apt-get update && apt-get install -y git make
                    elif command -v yum &> /dev/null; then
                        yum install -y git make
                    elif command -v dnf &> /dev/null; then
                        dnf install -y git make
                    elif command -v zypper &> /dev/null; then
                        zypper install -y git make
                    elif command -v pacman &> /dev/null; then
                        pacman -Sy git make
                    elif command -v apk &> /dev/null; then
                        apk add git make
                    else
                        echo "Unsupported package manager. Please install git manually."
                    fi

                    chmod u+w /root # This is required for vscode server
                    sleep infinity
                    """
            ]

            logger.debug("Setting security context")
            if not container.security_context:
                container.security_context = client.V1SecurityContext()
            container.security_context.run_as_user = 0

            container.security_context.privileged = True
            container.security_context.allow_privilege_escalation = True
            container.security_context.read_only_root_filesystem = False

            if not container.security_context.capabilities:
                container.security_context.capabilities = client.V1Capabilities()
            if not container.security_context.capabilities.add:
                container.security_context.capabilities.add = []
            container.security_context.capabilities.add.append(
                "SYS_ADMIN"
            )  # why not all in one list
            # Add all required capabilities
            container.security_context.capabilities.add.extend(
                [  # What is extend
                    "AUDIT_CONTROL",
                    "AUDIT_WRITE",
                    "BLOCK_SUSPEND",
                    "BPF",
                    "CHOWN",
                    "DAC_OVERRIDE",
                    "DAC_READ_SEARCH",
                    "FOWNER",
                    "FSETID",
                    "IPC_LOCK",
                    "IPC_OWNER",
                    "KILL",
                    "LEASE",
                    "LINUX_IMMUTABLE",
                    "MAC_ADMIN",
                    "MAC_OVERRIDE",
                    "MKNOD",
                    "NET_ADMIN",
                    "NET_BIND_SERVICE",
                    "NET_BROADCAST",
                    "NET_RAW",
                    "PERFMON",
                    "SETFCAP",
                    "SETGID",
                    "SETPCAP",
                    "SETUID",
                    "SYS_BOOT",
                    "SYS_CHROOT",
                    "SYS_MODULE",
                    "SYS_NICE",
                    "SYS_PACCT",
                    "SYS_PTRACE",
                    "SYS_RAWIO",
                    "SYS_RESOURCE",
                    "SYS_TIME",
                    "SYS_TTY_CONFIG",
                    "WAKE_ALARM",
                ]
            )

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

        if not container.env:
            container.env = []
        container.env.append(client.V1EnvVar(name="DEV_MODE", value="true"))
        container.env.append(client.V1EnvVar(name="DEBUG", value="true"))
        container.env.append(client.V1EnvVar(name="DD_ENV", value="dev"))
        container.env.append(
            client.V1EnvVar(name="DD_SERVICE", value=self.deployment_name)
        )
        container.env.append(client.V1EnvVar(name="DD_VERSION", value="latest"))

        logger.debug("Clearing pod security context")
        new_deployment.spec.template.spec.security_context = None

        logger.debug("Processing volumes and mounts")
        if new_deployment.spec.template.spec.volumes:
            volumes_to_keep = []
            for vol in new_deployment.spec.template.spec.volumes:
                if not (
                    vol.name.startswith("kube-api-access")
                    or vol.name == "eks-pod-identity-token"
                ):
                    volumes_to_keep.append(vol)
            new_deployment.spec.template.spec.volumes = volumes_to_keep

            logger.debug("Removing corresponding volume mounts")
            for container in new_deployment.spec.template.spec.containers:
                if container.volume_mounts:
                    container.volume_mounts = [
                        mount
                        for mount in container.volume_mounts
                        if not (
                            mount.name.startswith("kube-api-access")
                            or mount.name == "eks-pod-identity-token"
                        )
                    ]
        logger.debug("Adding PVC volume and mount")

        # Add a volume and volumeMount for PVC
        if new_deployment.spec.template.spec.volumes is None:
            new_deployment.spec.template.spec.volumes = []
        # Avoid duplicate volumes if re-creating workspace
        if not any(
            volume.name == "workspace"
            for volume in new_deployment.spec.template.spec.volumes
        ):
            new_deployment.spec.template.spec.volumes.append(
                client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self.pvc_name
                    ),
                )
            )

        # Add volume mount to all containers
        for container in new_deployment.spec.template.spec.containers:
            if container.volume_mounts is None:
                container.volume_mounts = []
            if not any(
                volume.name == "workspace" for volume in container.volume_mounts
            ):
                container.volume_mounts.append(
                    client.V1VolumeMount(name="workspace", mount_path=workspace_path)
                )

        return new_deployment

    @staticmethod
    def list_workspaces(namespace=None):
        Config.setup_kubernetes_client()
        """List all devmode workspaces across all namespaces."""
        logger.debug("Listing all devmode workspaces")

        try:
            logger.debug("Fetching secrets with tool=devmode label")
            # Get all secrets with tool=devmode label across all namespaces
            if namespace is None:
                secrets = Config.V1_API.list_secret_for_all_namespaces(
                    label_selector="tool=devmode"
                )
            else:
                secrets = Config.V1_API.list_namespaced_secret(
                    namespace=namespace, label_selector="tool=devmode"
                )

            logger.debug("Preparing table data")
            # Prepare data for table
            table_data = []
            results = []
            for secret in secrets.items:
                logger.debug(f"Processing secret {secret.metadata.name}")
                secret_name = secret.metadata.name
                namespace = secret.metadata.namespace
                # Workspace name is same as secret name
                workspace_name = (
                    base64.b64decode(secret.data["workspace_name"]).decode("utf-8")
                    if secret.data.get("workspace_name")
                    else None
                )
                original_deployment_name = (
                    base64.b64decode(secret.data["original_deployment_name"]).decode(
                        "utf-8"
                    )
                    if secret.data.get("original_deployment_name")
                    else None
                )
                table_data.append(
                    {
                        "Workspace Name": workspace_name,
                        "Original Deployment": original_deployment_name,
                        "Namespace": namespace,
                    }
                )

            logger.debug("Creating table with tabulate")
            # Create and display table using tabulate
            headers = ["Workspace Name", "Original Deployment", "Namespace"]
            from tabulate import tabulate

            table = tabulate(
                [list(item.values()) for item in table_data],
                headers=headers,
                tablefmt="grid",
            )
            # Add title and print table
            title = "\nWorkspace List\n"
            logger.info(f"{title}{table}\n")

            return table_data

        except client.exceptions.ApiException as e:
            logger.error(f"Failed to list workspaces: {e}")
            # sys.exit(1)

    @staticmethod
    def _get_workspace_from_secret(workspace_name, namespace):
        """Find the secret associated with a workspace.

        Args:
            workspace_name (str): Name of the workspace to find
            namespace (str): Namespace to search in

        Returns:
            str: Name of the secret if found

        Raises:
            SystemExit: If secret not found or API error occurs
        """
        logger.debug("Getting secrets with tool=devmode label")
        try:
            secrets = Config.V1_API.list_namespaced_secret(
                namespace=namespace, label_selector="tool=devmode"
            )

            logger.debug("Filtering secrets to find matching workspace")
            for secret in secrets.items:
                if secret.data.get("workspace_name"):
                    decoded_name = base64.b64decode(
                        secret.data["workspace_name"]
                    ).decode("utf-8")
                    if decoded_name == workspace_name:
                        logger.debug(f"Found secret for workspace {workspace_name}")
                        deployment_name = (
                            base64.b64decode(secret.data["deployment_name"]).decode(
                                "utf-8"
                            )
                            if secret.data.get("deployment_name")
                            else None
                        )
                        original_deployment_name = (
                            base64.b64decode(
                                secret.data.get("original_deployment_name")
                            ).decode()
                            if secret.data.get("original_deployment_name")
                            else None
                        )
                        pvc_name = (
                            base64.b64decode(secret.data.get("pvc_name")).decode()
                            if secret.data.get("pvc_name")
                            else None
                        )
                        workspace_path = (
                            base64.b64decode(secret.data["workspace_path"]).decode(
                                "utf-8"
                            )
                            if secret.data.get("workspace_path")
                            else None
                        )
                        service_name = (
                            base64.b64decode(secret.data["service_name"]).decode(
                                "utf-8"
                            )
                            if secret.data.get("service_name")
                            else None
                        )
                        ingress_name = (
                            base64.b64decode(secret.data["ingress_name"]).decode(
                                "utf-8"
                            )
                            if secret.data.get("ingress_name")
                            else None
                        )
                        logger.debug(
                            f"Deployment name: {original_deployment_name}, PVC name: {pvc_name}, workspace path: {workspace_path}"
                        )

                        return (
                            deployment_name,
                            original_deployment_name,
                            pvc_name,
                            workspace_path,
                            service_name,
                            ingress_name,
                            secret.metadata.name,
                        )

            logger.error(
                f"No workspace found with name {workspace_name} in namespace {namespace}"
            )
            # sys.exit(1)

        except client.exceptions.ApiException as e:
            logger.error(f"Failed to list secrets: {e}")
            # sys.exit(1)

    @staticmethod
    def _reconstruct_existing_workspace(workspace_name, namespace):
        Config.setup_kubernetes_client()

        Workspace.workspace_name = workspace_name
        Workspace.namespace = namespace
        (
            deployment_name,
            original_deployment_name,
            pvc_name,
            workspace_path,
            service_name,
            ingress_name,
            secret_name,
        ) = Workspace._get_workspace_from_secret(workspace_name, namespace)

        return Workspace(
            workspace_name, namespace, original_deployment_name, workspace_path
        )

    @staticmethod
    def _get_deployment_definition(deployment_name, namespace):
        logger.debug(
            f"Getting deployment definition for {deployment_name} in namespace {namespace}"
        )
        try:
            logger.debug("Fetching deployment definition from API")
            deployment = Config.APPS_API.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
            return deployment
        except client.exceptions.ApiException as e:
            logger.error(
                f"Error fetching deployment {deployment_name} in namespace {namespace}: {e}"
            )
            # exit(1)


if __name__ == "__main__":

    logger.debug("Starting script")
    fire.Fire(
        {
            "start": lambda workspace_name, namespace, deployment_name, workspace_path=None: Workspace(
                workspace_name, namespace, deployment_name, workspace_path
            ).start(),
            "list": Workspace.list_workspaces,
            "delete": lambda workspace_name, namespace: Workspace._reconstruct_existing_workspace(
                workspace_name, namespace
            ).delete(),
            "recreate": lambda workspace_name, namespace, workspace_path=None: Workspace._reconstruct_existing_workspace(
                workspace_name, namespace
            ).recreate(
                workspace_path
            ),
        }
    )
