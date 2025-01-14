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
        {
            "workspace_name": "sample-workspace",
            "deployment_name": "sample-deployment",
            "service_name": "sample-service",
            "workspace_path": "/code",
        },  # TODO / worksapce_path should not be here
        # {"workspace_name": "invalide_name", "deployment_name": "invalid_name", "service_name": "invalid_name", "workspace_path": "invalid_path"}, # TODO / make this passing
        # "sample-deployment-2"
    ],
)
def sample_workspace(temp_namespace, request):
    labels = {"app.kubernetes.io/instance": request.param["deployment_name"]}
    # Create a deployment
    apps_api = client.AppsV1Api()
    core_api = client.CoreV1Api()
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=request.param["deployment_name"],
            namespace=temp_namespace,
            labels=labels,
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
            name=request.param["service_name"], namespace=temp_namespace, labels=labels
        ),
        spec=client.V1ServiceSpec(
            selector=labels,
            ports=[
                client.V1ServicePort(port=80, target_port=80)
            ],  # Does not matteer for now
            type="ClusterIP",
        ),
    )

    apps_api.create_namespaced_deployment(namespace=temp_namespace, body=deployment)
    core_api.create_namespaced_service(namespace=temp_namespace, body=service)

    yield request.param["workspace_name"], request.param[
        "deployment_name"
    ], request.param["service_name"], request.param["workspace_path"], temp_namespace

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


class TestWorkspace:

    def test_start(self, sample_workspace):
        """Test starting a workspace"""
        (
            workspace_name,
            deployment_name,
            service_name,
            workspace_path,
            temp_namespace,
        ) = sample_workspace
        workspace = devmode.Workspace(
            workspace_name,
            temp_namespace,
            deployment_name,
            workspace_path,
        )
        (
            new_workspace_name,
            new_deployment_name,
            new_pvc_name,
            new_service_name,
            new_secret_name,
        ) = workspace.start()
        assert (
            deployment_name in new_deployment_name
            and deployment_name != new_deployment_name
        )
        assert service_name in new_service_name and service_name != new_service_name

        assert (
            deployment_name in new_secret_name and deployment_name != new_secret_name
        )  # named after deployment
        assert (
            deployment_name in new_pvc_name and deployment_name != new_pvc_name
        )  # named after deployment
        assert new_workspace_name == workspace_name

        # Assert that the deployment has all pods running
        from kubernetes import watch

        V1_API = client.CoreV1Api()

        w = watch.Watch()
        for event in w.stream(
            V1_API.list_namespaced_pod,
            namespace=temp_namespace,
            label_selector=f"app.kubernetes.io/instance={deployment_name}",
            timeout_seconds=40,
        ):
            pod = event["object"]
            if pod.status.phase == "Running":
                break
        else:
            for pod in V1_API.list_namespaced_pod(
                namespace=temp_namespace,
                label_selector=f"app.kubernetes.io/instance={deployment_name}",
            ).items:
                assert (
                    pod.status.phase == "Running"
                ), f"Pod {pod.metadata.name} is not running"

    def test_list(self, sample_workspace, caplog):
        """Test listing workspaces"""
        (
            workspace_name,
            deployment_name,
            service_name,
            workspace_path,
            temp_namespace,
        ) = sample_workspace
        # Verify the workspace shows up in list
        result = devmode.Workspace.list_workspaces(temp_namespace)

        assert len(result) == 1  # created by the test_start
        assert workspace_name in result[0]["Workspace Name"]
        assert "Workspace Name" in caplog.text

    def test_recreate(self, sample_workspace):
        """Test recreating a workspace"""
        (
            workspace_name,
            deployment_name,
            service_name,
            workspace_path,
            temp_namespace,
        ) = sample_workspace
        devmode.Workspace._reconstruct_existing_workspace(
            workspace_name, temp_namespace
        ).recreate()

    def test_delete(self, sample_workspace):
        """Test deleting a workspace"""
        (
            workspace_name,
            deployment_name,
            service_name,
            workspace_path,
            temp_namespace,
        ) = sample_workspace
        devmode.Workspace._reconstruct_existing_workspace(
            workspace_name, temp_namespace
        ).delete()
        # Verify workspace no longer exists
