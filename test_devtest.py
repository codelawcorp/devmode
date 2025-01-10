import pytest
import uuid
import devmode
from kubernetes import client

devmode.Config.setup_kubernetes_client()


### FIXTURES ### START
@pytest.fixture(scope="class")
def temp_namespace():
    # Create unique namespace name with pytest- prefix
    namespace = f"pytest-{uuid.uuid4().hex[:8]}"

    # Create namespace
    api = client.CoreV1Api()
    ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    api.create_namespace(ns)

    yield namespace

    # Cleanup - delete namespace and all resources in it
    try:
        api.delete_namespace(namespace)
    except client.rest.ApiException:
        pass  # Namespace may already be deleted


@pytest.fixture(
    scope="class",
    params=[
        "sample-deployment",
        # "sample-deployment-2"
    ],
)
def sample_deployment(temp_namespace, request):
    labels = {"app.kubernetes.io/instance": request.param}
    # Create a deployment
    apps_api = client.AppsV1Api()
    core_api = client.CoreV1Api()
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=request.param, namespace=temp_namespace, labels=labels
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels=labels),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="test-container",
                            image="nginx:latest",
                            ports=[client.V1ContainerPort(container_port=80)],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "200m", "memory": "256Mi"},
                            ),
                        )
                    ]
                ),
            ),
        ),
    )
    # Create deployment in cluster

    service = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=request.param, namespace=temp_namespace, labels=labels
        ),
        spec=client.V1ServiceSpec(
            selector=labels,
            ports=[client.V1ServicePort(port=80, target_port=80)],
            type="ClusterIP",
        ),
    )

    apps_api.create_namespaced_deployment(namespace=temp_namespace, body=deployment)
    core_api.create_namespaced_service(namespace=temp_namespace, body=service)

    yield deployment.metadata.name, temp_namespace

    # Cleanup
    try:
        apps_api.delete_namespaced_deployment(
            name=request.param, namespace=temp_namespace
        )
    except client.rest.ApiException:
        pass  # Deployment may already be deleted


### FIXTURES ### END

# def test_nothing():
#     assert True, "is obvious"


class TestDevmode:
    workspace_name = "test-workspace"

    def test_start(self, sample_deployment):
        """Test starting a workspace"""
        deployment, temp_namespace = sample_deployment
        workspace = devmode.Workspace(
            "test-workspace",
            temp_namespace,
            deployment,
            workspace_path="/code",
        )
        workspace_name, deployment_name, pvc_name, service_name, secret_name = (
            workspace.start()
        )
        assert deployment in deployment_name and deployment != deployment_name
        assert deployment in service_name and deployment != service_name
        assert deployment in secret_name and deployment != secret_name
        assert deployment in pvc_name and deployment != pvc_name
        assert workspace_name == "test-workspace"

        # Assert that the deployment has all pods running
        from kubernetes import watch

        V1_API = client.CoreV1Api()

        w = watch.Watch()
        for event in w.stream(
            V1_API.list_namespaced_pod,
            namespace=temp_namespace,
            label_selector=f"app={deployment}",
            timeout_seconds=40,
        ):
            pod = event["object"]
            if pod.status.phase == "Running":
                break
        else:
            for pod in V1_API.list_namespaced_pod(
                namespace=temp_namespace, label_selector=f"app={deployment}"
            ).items:
                assert (
                    pod.status.phase == "Running"
                ), f"Pod {pod.metadata.name} is not running"

        # self.workspace_name = workspace_name

    def test_list(self, sample_deployment, caplog):
        """Test listing workspaces"""
        deployment, temp_namespace = sample_deployment
        # Verify the workspace shows up in list
        result = devmode.Workspace.list_workspaces(temp_namespace)

        assert len(result) == 1  # created by the test_start
        assert self.workspace_name in result[0]["Workspace Name"]
        assert "Workspace Name" in caplog.text

    def test_recreate(self, sample_deployment):
        """Test recreating a workspace"""
        deployment, temp_namespace = sample_deployment
        devmode.Workspace._existing_workspace(
            self.workspace_name, temp_namespace
        ).recreate()

    def test_delete(self, sample_deployment):
        """Test deleting a workspace"""
        deployment, temp_namespace = sample_deployment
        devmode.Workspace._existing_workspace(
            self.workspace_name, temp_namespace
        ).delete()
        # Verify workspace no longer exists
