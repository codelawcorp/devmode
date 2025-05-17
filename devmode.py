#! /usr/bin/env python3
import os, sys

import fire
import yaml
from logger import logger
from kubernetes import client, config
import base64
import time


class Config:
    V1_API = None
    APPS_API = None
    NETWORKING_API = None

    PARSED_CONFIG = None

    def __init__(self):
        self.setup_kubernetes_client()
        self.parse_config_yaml()
        pass

    @classmethod
    def setup_kubernetes_client(cls):
        logger.debug("Setting up logging and attempting to load kubeconfig")
        try:
            kubeconfig = os.getenv("KUBECONFIG", "~/.kube/config")
            logger.debug(f"Using kubeconfig path: {kubeconfig}")
            config.load_kube_config(config_file=kubeconfig)

            cls.V1_API = client.CoreV1Api()
            cls.APPS_API = client.AppsV1Api()
            cls.NETWORKING_API = client.NetworkingV1Api()

            try:
                # Test access to the cluster
                cls.V1_API.list_namespace()
                logger.debug("Successfully loaded kubeconfig")
            except Exception as e:
                logger.error(f"Failed to access cluster")
                logger.debug(f"Error: {e}")
                raise
                # sys.exit(1)
        except Exception as e:
            logger.error(f"Error loading kubeconfig")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

    @classmethod
    def parse_config_yaml(cls):
        # Get the pass relative to this file
        base_path = os.path.dirname(os.path.realpath(__file__))
        logger.debug("Parsing config.yaml")
        try:
            with open(f"{base_path}/config.yaml", "r") as file:
                config_data = yaml.safe_load(file)
                logger.debug("Successfully parsed config.yaml")
                cls.PARSED_CONFIG = config_data
                return config_data
        except Exception as e:
            logger.error("Failed to parse config.yaml")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)


class Workspace:
    """TEST"""

    @property
    def workspace_name(self):
        return self._workspace_name

    @workspace_name.setter
    def workspace_name(self, value):
        if not value:
            raise ValueError("Workspace name value must not be empty")
        self._workspace_name = value

    @property
    def namespace(self):
        return self._namespace

    @namespace.setter
    def namespace(self, value):
        if not value:
            logger.warning("Namespace not provided, using default")
            self._namespace = "default"
        else:
            self._namespace = value

    @property
    def original_deployment_name(self):
        return self._original_deployment_name

    @original_deployment_name.setter
    def original_deployment_name(self, value):
        self._original_deployment_name = value

    @property
    def workspace_path(self):
        return self._workspace_path

    @workspace_path.setter
    def workspace_path(self, value):
        self._workspace_path = value

    def __init__(self, workspace_name, namespace, deployment_name, workspace_path=None):
        self.workspace_name = workspace_name
        self.namespace = namespace
        self.original_deployment_name = deployment_name
        self.workspace_path = workspace_path or "/app"
        self.service_name = None
        self.ingress_name = None
        self.ingress_host = None

        self.prefix = f"devmode-{workspace_name}"
        self.deployment_name = f"{self.original_deployment_name}-{self.prefix}"
        self.pvc_name = f"{self.original_deployment_name}-{self.prefix}"
        self.secret_name = f"{self.original_deployment_name}-{self.prefix}"
        self.labels = {
            "app.kubernetes.io/instance": self.deployment_name,
            "tool": "devmode",
            "workspace": self.workspace_name,
            "tags.datadoghq.com/env": "dev",
            "tags.datadoghq.com/service": self.deployment_name,
            "tags.datadoghq.com/version": "latest",
        }
        self.annotations = {
            "com.datadoghq.ad.tags": f'["workspace_name:{workspace_name}",]'
        }

        Config()

    @staticmethod
    def start_new(workspace_name, namespace, deployment_name, workspace_path=None):
        """Start a devmode workspace.

        Args:
            workspace_name (str): Name of the workspace
            namespace (str): Namespace to create the workspace in
            deployment_name (str): Name of the deployment to create the workspace for
            workspace_path (str): Path to the workspace in the container (default: /app)
        """
        workspace = Workspace(
            workspace_name, namespace, deployment_name, workspace_path
        )
        workspace.start()

    def start(self):
        original_deployment_raw = self._get_deployment_definition(
            self.original_deployment_name, self.namespace
        )

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
                logger.info(f"PVC {pvc.metadata.name} already exists")
                pvc_result = Config.V1_API.read_namespaced_persistent_volume_claim(
                    name=pvc.metadata.name, namespace=self.namespace
                )
                logger.debug(f"Retrieved existing PVC {pvc.metadata.name}")
            else:
                logger.error(f"Failed to create PVC")
                logger.debug(f"Error: {e}")
                raise
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
                    logger.error(f"Failed to create service: {self.service_name}")
                    logger.debug(f"Error: {e}")
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
            original_ingress_host = original_ingress.spec.rules[0].host
            self.ingress_name = f"{original_ingress.metadata.name}-{self.prefix}"

            # Create a copy of the ingress with new labels and name
            new_ingress = original_ingress
            new_ingress.metadata.name = self.ingress_name
            new_ingress.metadata.labels = self.labels
            new_ingress.metadata.resource_version = None
            # If actual ingress host matches expected domain
            if (
                Config.PARSED_CONFIG.get("base_domain")
                in original_ingress_host
            ):
                self.ingress_host =  f"{self.prefix}.{Config.PARSED_CONFIG.get('base_domain')}"
                
                logger.debug(f"Using ingress host: {self.ingress_host}")
            else:
                logger.warning(
                    f"Base domain from configfile is not a substring of the actual ingress host. The configured base domain is {Config.PARSED_CONFIG.get('base_domain')}. The found ingress hostname is {original_ingress_host}. Attempting to use the original ingress host with '{self.workspace_name}' subdomain."
                )
                self.ingress_host = f"{self.workspace_name}.{original_ingress_host}"
                logger.debug(f"Using ingress host: {self.ingress_host}")
            # Assuming there is only one rule
            new_ingress.spec.rules[0].host = self.ingress_host
            # Assuming there is only one path
            for path in new_ingress.spec.rules[0].http.paths:
                if path.path == "/":
                    path.backend.service.name = self.service_name

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
                    logger.error(f"Failed to create ingress: {self.ingress_name}")
                    logger.debug(f"Error: {e}")
                    raise
        # Create deployment in cluster
        deployment_raw = self._modify_deployment_for_dev_mode(
            original_deployment_raw, self.ingress_host, self.workspace_path
        )
        logger.debug("Dumping deployment definition to YAML")
        logger.debug(yaml.dump(deployment_raw.to_dict(), default_flow_style=False))

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
                    logger.error(f"The workspace is incomplete and cannot be started")
                    logger.error(f"Failed to cleanup PVC {self.pvc_name}")
                    logger.debug(f"Error: {e}")
                    raise
                    # sys.exit(1)

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
                "ingress_host": self.ingress_host,
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
                logger.error(f"Failed to create secret: {self.secret_name}")
                logger.debug(f"Error: {e}")
                raise
                # exit(1)

        logger.info(f"Workspace {self.workspace_name} started successfully")
        if self.ingress_host:
            logger.info(
                f"\033[32mAccess your workspace at: https://{self.ingress_host}\033[0m"
            )
        for env_var in original_deployment_raw.spec.template.spec.containers[0].env:
            if "GIT_REPOSITORY_URL" == env_var.name:
                self.git_repo_url = env_var.value
                logger.info(
                    f"When attach to workspace's pod with vscode for the first time run: `cd {self.workspace_path}; rm -rf lost+found ; git clone {self.git_repo_url} . `"
                )
                break
            # open this in browser
            # os.system(f"open https://{self.ingress_host}")
        return (
            self.workspace_name,
            self.deployment_name,
            self.pvc_name,
            self.service_name,
            self.ingress_name,
            self.secret_name,
        )

    @staticmethod
    def delete_existing(workspace_name, namespace, keep_pvc=False):
        """Delete an existing devmode workspace and associated resources.

        Args:
            workspace_name (str): Name of the secret/workspace to delete
            namespace (str): Namespace where the workspace exists (default: default)
        """
        logger.debug(f"Deleting workspace {workspace_name} in namespace {namespace}")

        try:
            workspace = Workspace._reconstruct_existing_workspace(
                workspace_name, namespace
            )
            workspace.delete(keep_pvc=keep_pvc)
        except client.exceptions.ApiException as e:
            logger.error(f"Failed to delete workspace: {workspace_name}")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

    def delete(self, keep_pvc=False):
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
                        logger.error(
                            f"Failed to delete deployment: {self.deployment_name}"
                        )
                        logger.debug(f"Error: {e}")
                        raise

            if self.pvc_name and keep_pvc is False:
                logger.debug(f"Attempting to delete PVC {self.pvc_name}")
                # Delete PVC
                try:
                    Config.V1_API.delete_namespaced_persistent_volume_claim(
                        name=self.pvc_name, namespace=self.namespace
                    )
                    logger.debug(
                        f"Waiting for PVC {self.pvc_name} to be completely deleted"
                    )
                    while True:
                        try:
                            Config.V1_API.read_namespaced_persistent_volume_claim(
                                name=self.pvc_name, namespace=self.namespace
                            )
                            logger.debug(
                                f"PVC {self.pvc_name} still exists, waiting..."
                            )
                            time.sleep(2)
                        except client.exceptions.ApiException as e:
                            if e.status == 404:
                                break
                            else:
                                logger.error(
                                    f"Error while waiting for PVC deletion: {e}"
                                )
                                raise
                    logger.info(f"Deleted PVC {self.pvc_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(f"PVC {self.pvc_name} already deleted")
                    else:
                        logger.error(f"Failed to delete PVC: {self.pvc_name}")
                        logger.debug(f"Error: {e}")
                        raise

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
                        logger.error(f"Failed to delete service: {self.service_name}")
                        logger.debug(f"Error: {e}")
                        raise

            # Delete ingress
            if self.ingress_name:
                logger.debug(f"Attempting to delete ingress {self.ingress_name}")
                try:
                    Config.NETWORKING_API.delete_namespaced_ingress(
                        name=self.ingress_name, namespace=self.namespace
                    )

                    logger.debug(
                        f"Waiting for ingress {self.ingress_name} to be completely deleted"
                    )
                    while True:
                        try:
                            Config.NETWORKING_API.read_namespaced_ingress(
                                name=self.ingress_name, namespace=self.namespace
                            )
                            logger.debug(
                                f"Ingress {self.ingress_name} still exists, waiting..."
                            )
                            time.sleep(2)
                        except client.exceptions.ApiException as e:
                            if e.status == 404:
                                break
                            else:
                                logger.error(
                                    f"Error while waiting for ingress deletion: {e}"
                                )
                                raise
                    logger.info(f"Deleted ingress {self.ingress_name}")
                except client.exceptions.ApiException as e:
                    if e.status == 404:
                        logger.warning(f"Ingress {self.ingress_name} already deleted")
                    else:
                        logger.error(f"Failed to delete ingress: {self.ingress_name}")
                        logger.debug(f"Error: {e}")
                        raise

            # Delete the secret itself
            logger.debug(f"Attempting to delete secret {self.secret_name}")
            try:
                Config.V1_API.delete_namespaced_secret(
                    name=self.secret_name, namespace=self.namespace
                )
                logger.info(f"Deleted secret {self.secret_name}")

                return (
                    self.workspace_name,
                    self.deployment_name,
                    self.pvc_name,
                    self.service_name,
                    self.ingress_name,
                    self.secret_name,
                )
            except client.exceptions.ApiException as e:
                if e.status == 404:
                    logger.warning(f"Secret {self.secret_name} already deleted")
                else:
                    logger.error(f"Failed to delete secret: {self.secret_name}")
                    logger.debug(f"Error: {e}")
                    raise

        except client.exceptions.ApiException as e:
            logger.error(f"Failed to delete workspace: {self.workspace_name}")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

    @staticmethod
    def recreate_existing(
        workspace_name, namespace, keep_pvc=True, new_workspace_path=None
    ):
        """Recreate an existing devmode workspace.

        Args:
            workspace_name (str): Name of the secret/workspace to recreate
            namespace (str): Namespace where the workspace exists (default: default)
            new_workspace_path (str): New workspace path to use (reuses existing by default)
        """
        logger.debug(f"Recreating workspace {workspace_name} in namespace {namespace}")

        try:
            workspace = Workspace._reconstruct_existing_workspace(
                workspace_name, namespace
            )
            workspace.recreate(keep_pvc, new_workspace_path)
        except client.exceptions.ApiException as e:
            logger.error(f"Failed to recreate workspace: {workspace_name}")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

    def recreate(self, keep_pvc=True, new_workspace_path=None):
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
                ingress_host,
                secret_name,
            ) = self._get_workspace_from_secret(self.workspace_name, self.namespace)
            logger.debug(
                f"Retrieved original deployment name from secret: {original_deployment_name}"
            )
        except client.exceptions.ApiException as e:
            logger.error(f"Failed to get secret for workspace {self.workspace_name}")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

        # Delete existing workspace
        self.delete(keep_pvc=keep_pvc)

        # Start new workspace
        self.workspace_path = new_workspace_path or workspace_path
        self.start()

        logger.info(f"Successfully recreated workspace {self.workspace_name}")

    def _modify_deployment_for_dev_mode(
        self, old_deployment, ingress_host, workspace_path
    ):
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

        logger.debug("Modifying containers")
        for container in new_deployment.spec.template.spec.containers:
            logger.debug(f"Changing `{container.name}` container's command")
            container.command = ["/bin/sh", "-c"]
            logger.debug(f"Changing `{container.name}` container's args")
            container.args = [
                """
                    if command -v apt-get &> /dev/null; then
                        apt-get update && apt-get install -y git make awscli
                    elif command -v yum &> /dev/null; then
                        yum install -y git make awscli
                    elif command -v dnf &> /dev/null; then
                        dnf install -y git make awscli
                    elif command -v zypper &> /dev/null; then
                        zypper install -y git make awscli
                    elif command -v pacman &> /dev/null; then
                        pacman -Sy git make aws-cli
                    elif command -v apk &> /dev/null; then
                        apk add git make aws-cli
                    else
                        echo "Unsupported package manager. Please install git and aws-cli manually."
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
            container.startup_probe = None

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
        container.env.append(client.V1EnvVar(name="LOG_LEVEL", value="DEBUG"))
        container.env.append(client.V1EnvVar(name="DD_ENV", value="dev"))
        container.env.append(
            client.V1EnvVar(name="DD_SERVICE", value=self.original_deployment_name)
        )
        container.env.append(client.V1EnvVar(name="DD_VERSION", value="latest"))
        container.env.append(client.V1EnvVar(name="APP_HOSTNAME", value=ingress_host))

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
        """List all devmode workspaces across all namespaces."""
        Config()
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
                ingress_host = (
                    base64.b64decode(secret.data["ingress_host"]).decode("utf-8")
                    if secret.data.get("ingress_host")
                    else None
                )
                table_data.append(
                    {
                        "Workspace Name": workspace_name,
                        "Original Deployment": original_deployment_name,
                        "Namespace": namespace,
                        "Ingress Host": ingress_host and f"https://{ingress_host}",
                    }
                )

            logger.debug("Creating table with tabulate")
            # Create and display table using tabulate
            headers = [
                "Workspace Name",
                "Original Deployment",
                "Namespace",
                "Ingress Host",
            ]
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
            logger.error(f"Failed to list workspaces")
            logger.debug(f"Error: {e}")
            raise
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
                        ingress_host = (
                            base64.b64decode(secret.data["ingress_host"]).decode(
                                "utf-8"
                            )
                            if secret.data.get("ingress_host")
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
                            ingress_host,
                            secret.metadata.name,
                        )

            logger.error(
                f"No workspace found with name {workspace_name} in namespace {namespace}"
            )
            raise ValueError("Workspace not found")
            # sys.exit(1)

        except client.exceptions.ApiException as e:
            logger.error(f"Failed to list secrets")
            logger.debug(f"Error: {e}")
            raise
            # sys.exit(1)

    @staticmethod
    def _reconstruct_existing_workspace(workspace_name, namespace):
        Config()
        Workspace.workspace_name = workspace_name
        Workspace.namespace = namespace
        (
            deployment_name,
            original_deployment_name,
            pvc_name,
            workspace_path,
            service_name,
            ingress_name,
            ingress_host,
            secret_name,
        ) = Workspace._get_workspace_from_secret(workspace_name, namespace)

        workspace = Workspace(
            workspace_name, namespace, original_deployment_name, workspace_path
        )

        workspace.pvc_name = pvc_name
        workspace.service_name = service_name
        workspace.ingress_name = ingress_name
        workspace.ingress_host = ingress_host
        workspace.secret_name = secret_name

        return workspace

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
                f"Error fetching deployment {deployment_name} in namespace {namespace}"
            )
            logger.debug(f"Error: {e}")
            raise
            # exit(1)


if __name__ == "__main__":
    try:
        logger.debug("Starting script")
        fire.Fire(
            {
                "start": Workspace.start_new,
                "list": Workspace.list_workspaces,
                "delete": Workspace.delete_existing,
                "recreate": Workspace.recreate_existing,
            }
        )
    except Exception as e:
        # logger.error(f"Error running script: {e}")
        logger.error(
            f"Something went wrong. Prease run with LOG_LEVEL=DEBUG env var to see more details."
        )
        sys.exit(1)
