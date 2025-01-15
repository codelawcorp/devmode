import pytest
import uuid
from devmode import Config, Workspace
from kubernetes import client

Config.setup_kubernetes_client()


### FIXTURES ### START
@pytest.fixture(scope="class")
def temp_namespace():
    # Create unique namespace name with pytest- prefix
    namespace = f"pytest-{uuid.uuid4().hex[:8]}"

    # Create namespace
    ns = client.V1Namespace(metadata=client.V1ObjectMeta(name=namespace))
    Config.V1_API.create_namespace(ns)

    yield namespace

    # Cleanup - delete namespace and all resources in it
    try:
        Config.V1_API.delete_namespace(namespace)
    except client.rest.ApiException:
        pass  # Namespace may already be deleted


@pytest.fixture(
    scope="class",
    params=[
        {
            "workspace_name": "sample-workspace",
            "deployment_name": "sample-deployment",
            "service_name": "sample-service",
            "ingress_name": "sample-ingress",
            "workspace_path": "/code",
            "keep_pvc": False,
            "labels": {
                "app.kubernetes.io/instance": "sample-deployment",
                "tags.datadoghq.com/env": "dev",
                "tags.datadoghq.com/service": "sample-deployment",
                "tags.datadoghq.com/version": "1.0.0",
            },
            "envVars": [
                {"name": "SOME_KEY", "value": "SOME_VALUE"},
            ],
        },
        {
            "workspace_name": "sample-workspace-2",
            "deployment_name": "sample-deployment-2",
            "service_name": "sample-service-2",
            "ingress_name": None,  # Try without ingress
            "workspace_path": None,  # Try without workspace path
            "keep_pvc": True,
            "labels": {
                "app.kubernetes.io/instance": "sample-deployment-2",  #  MUST Match deployment name
                "tags.datadoghq.com/env": "dev",
                "tags.datadoghq.com/service": "sample-deployment-2",  #  MUST Match deployment name
                "tags.datadoghq.com/version": "1.0.0",
            },
            "envVars": None,  # Try without env vars
        },
    ],
)
def sample_workspace(temp_namespace, request):
    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=request.param["deployment_name"],
            namespace=temp_namespace,
            labels=request.param["labels"],
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels=request.param["labels"]),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=request.param["labels"]),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="test-container",
                            image="nginx:latest",
                            ports=[client.V1ContainerPort(container_port=80)],
                            env=request.param["envVars"],
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
    Config.APPS_API.create_namespaced_deployment(
        namespace=temp_namespace, body=deployment
    )

    service = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=request.param["service_name"],
            namespace=temp_namespace,
            labels=request.param["labels"],
        ),
        spec=client.V1ServiceSpec(
            selector=request.param["labels"],
            ports=[
                client.V1ServicePort(port=80, target_port=80)
            ],  # Does not matteer for now
            type="ClusterIP",
        ),
    )
    Config.V1_API.create_namespaced_service(namespace=temp_namespace, body=service)

    if request.param["ingress_name"]:
        ingress = client.V1Ingress(
            metadata=client.V1ObjectMeta(
                name=request.param["ingress_name"],
                namespace=temp_namespace,
                labels=request.param["labels"],
            ),
            spec=client.V1IngressSpec(
                rules=[
                    client.V1IngressRule(
                        host="example.com",
                        http=client.V1HTTPIngressRuleValue(
                            paths=[
                                client.V1HTTPIngressPath(
                                    path="/",
                                    path_type="Prefix",
                                    backend=client.V1IngressBackend(
                                        service=client.V1IngressServiceBackend(
                                            name=request.param["service_name"],
                                            port={"port": "http", "number": 80},
                                        )
                                    ),
                                )
                            ]
                        ),
                    )
                ]
            ),
        )
        Config.NETWORKING_API.create_namespaced_ingress(
            namespace=temp_namespace, body=ingress
        )

    yield (
        request.param["workspace_name"],
        temp_namespace,
        request.param["deployment_name"],
        request.param["service_name"],
        request.param["ingress_name"],
        request.param["workspace_path"],
        request.param["keep_pvc"],
    )

    # Cleanup
    try:
        Config.APPS_API.delete_namespaced_deployment(
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
            temp_namespace,
            deployment_name,
            service_name,
            ingress_name,
            workspace_path,
            keep_pvc,
        ) = sample_workspace
        workspace = Workspace(
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
            new_ingress_name,
            new_secret_name,
        ) = workspace.start()
        assert (
            deployment_name in new_deployment_name
            and deployment_name != new_deployment_name
        )
        assert service_name in new_service_name and service_name != new_service_name
        if ingress_name:
            assert ingress_name in new_ingress_name and ingress_name != new_ingress_name

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
            temp_namespace,
            deployment_name,
            service_name,
            ingress_name,
            workspace_path,
            keep_pvc,
        ) = sample_workspace
        # Verify the workspace shows up in list
        result = Workspace.list_workspaces(temp_namespace)

        assert len(result) == 1  # created by the test_start
        assert workspace_name in result[0]["Workspace Name"]
        assert "Workspace Name" in caplog.text

    def test_recreate(self, sample_workspace):
        """Test recreating a workspace"""
        (
            workspace_name,
            temp_namespace,
            deployment_name,
            service_name,
            ingress_name,
            workspace_path,
            keep_pvc,
        ) = sample_workspace

        Workspace._reconstruct_existing_workspace(
            workspace_name, temp_namespace
        ).recreate()

    def test_delete(self, sample_workspace):
        """Test deleting a workspace"""
        (
            workspace_name,
            temp_namespace,
            deployment_name,
            service_name,
            ingress_name,
            workspace_path,
            keep_pvc,
        ) = sample_workspace

        (
            deleted_workspace_name,
            deleted_deployment_name,
            deleted_pvc_name,
            deleted_service_name,
            deleted_ingress_name,
            deleted_secret_name,
        ) = Workspace._reconstruct_existing_workspace(
            workspace_name, temp_namespace
        ).delete(
            keep_pvc=keep_pvc
        )

        assert deleted_workspace_name == workspace_name
        assert deployment_name in deleted_deployment_name
        assert service_name in deleted_service_name
        if ingress_name:
            assert ingress_name in deleted_ingress_name
        assert deployment_name in deleted_secret_name
        assert deployment_name in deleted_pvc_name

        # Check if deployment exists
        try:

            deployment_found = Config.APPS_API.read_namespaced_deployment(
                name=deleted_deployment_name, namespace=temp_namespace
            )
            assert (
                deployment_found is None
            ), f"Deployment {deleted_deployment_name} still present"
        except client.rest.ApiException as e:
            if e.status == 404:
                pass  # Deployment not found is expected
            else:
                assert (
                    False
                ), f"Failed to read deployment {deleted_deployment_name}: {e}"

        # Check if service exists
        try:
            service_found = Config.V1_API.read_namespaced_service(
                name=deleted_service_name, namespace=temp_namespace
            )
            assert (
                service_found is None
            ), f"Service {deleted_service_name} still present"
        except client.rest.ApiException as e:
            if e.status == 404:
                pass  # Service not found is expected
            else:
                assert False, f"Failed to read service {deleted_service_name}"

        # Check if secret exists
        try:
            secret_found = Config.V1_API.read_namespaced_secret(
                name=deleted_deployment_name, namespace=temp_namespace
            )
            assert (
                secret_found is None
            ), f"Secret {deleted_deployment_name} still present"
        except client.rest.ApiException as e:
            if e.status == 404:
                pass  # Service not found is expected
            else:
                assert False, f"Failed to read secret {deleted_deployment_name}: {e}"

        # Check if pvc exists
        if not keep_pvc:
            try:
                pvc_found = Config.V1_API.read_namespaced_persistent_volume_claim(
                    name=deleted_pvc_name, namespace=temp_namespace
                )
                assert pvc_found is not None, f"PVC {deleted_pvc_name} still present"
            except client.rest.ApiException as e:
                if e.status == 404:
                    pass  # PVC not found is expected
                else:
                    assert False, f"Failed to read PVC {deleted_pvc_name}: {e}"

        # Check if ingress exists
        if ingress_name:
            try:
                network = client.NetworkingV1Api()
                ingress_found = network.read_namespaced_ingress(
                    name=deleted_ingress_name, namespace=temp_namespace
                )
                assert (
                    ingress_found is not None
                ), f"Ingress {deleted_ingress_name} still present"
            except client.rest.ApiException as e:
                if e.status == 404:
                    pass  # Ingress not found is expected
                else:
                    assert False, f"Failed to read ingress {deleted_ingress_name}: {e}"
