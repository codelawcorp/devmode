import pytest
import uuid
import devmode
from kubernetes import client

@pytest.fixture(scope="class")
def temp_namespace():
    # Create unique namespace name with pytest- prefix
    namespace = f"pytest-{uuid.uuid4().hex[:8]}"
    
    # Create namespace
    api = client.CoreV1Api()
    ns = client.V1Namespace(
        metadata=client.V1ObjectMeta(name=namespace)
    )
    api.create_namespace(ns)

    yield namespace

    # Cleanup - delete namespace and all resources in it
    try:
        api.delete_namespace(namespace)
    except client.rest.ApiException:
        pass  # Namespace may already be deleted



@pytest.fixture(scope="class")
def sample_deployment(temp_namespace):
    # Create a deployment
    apps_api = client.AppsV1Api()
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name="sample-deployment",
            namespace=temp_namespace,
            labels={"app": "sample-app"}
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": "sample-app"}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "sample-app"}
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="test-container",
                            image="nginx:latest",
                            ports=[client.V1ContainerPort(container_port=80)],
                            resources=client.V1ResourceRequirements(
                                requests={
                                    "cpu": "100m",
                                    "memory": "128Mi"
                                },
                                limits={
                                    "cpu": "200m",
                                    "memory": "256Mi"
                                }
                            )
                        )
                    ]
                )
            )
        )
    )
    # Create deployment in cluster
    apps_api.create_namespaced_deployment(
        namespace=temp_namespace,
        body=deployment
    )

    yield deployment.metadata.name, temp_namespace

    # Cleanup
    try:
        apps_api.delete_namespaced_deployment(
            name="sample-deployment",
            namespace=temp_namespace
        )
    except client.rest.ApiException:
        pass  # Deployment may already be deleted



# def test_nothing():
#     assert True, "is obvious"

    
class TestDevmode:
    workspace_name = "test-workspace"
    def test_start(self, sample_deployment):
        """Test starting a workspace"""
        deployment, temp_namespace = sample_deployment
        workspace_name, deployment_name, pvc_name, secret_name = devmode.start_workspace(
            deployment,
            temp_namespace,
            workspace_name=f"test-workspace",
            workspace_path="/code"
        )
        assert deployment in deployment_name
        assert deployment in secret_name
        assert deployment in pvc_name
        assert workspace_name =="test-workspace"

        # self.workspace_name = workspace_name

    def test_list(self, sample_deployment, caplog):
        """Test listing workspaces"""
        deployment, temp_namespace = sample_deployment
        # Verify the workspace shows up in list
        result = devmode.list_workspaces(temp_namespace)

        assert len(result) == 1 # created by the test_start
        assert self.workspace_name in result[0]["Workspace Name"]
        assert "Workspace Name" in caplog.text

    def test_recreate(self, sample_deployment):
        """Test recreating a workspace"""
        deployment, temp_namespace = sample_deployment
        devmode.recreate_workspace(
            self.workspace_name,
            temp_namespace
        )

    def test_delete(self, sample_deployment):
        """Test deleting a workspace"""
        deployment, temp_namespace = sample_deployment
        devmode.delete_workspace(
            self.workspace_name,
            temp_namespace
        )
        # Verify workspace no longer exists

